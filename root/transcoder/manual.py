#!/usr/bin/env python3

import sys
import os
import guessit
import locale
import glob
import argparse
import struct
import enum
import logging
import shutil
import tmdbsimple as tmdb
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.mediaprocessor import MediaProcessor
from resources.metadata import Metadata, MediaType
from resources.postprocess import PostProcessor
from resources.extensions import tmdbApiKey
from converter.avcodecs import audio_codec_list, video_codec_list, subtitle_codec_list, attachment_codec_list

if sys.version[0] == "3":
    raw_input = input

os.environ["REGEX_DISABLED"] = "1"  # Fixes Toilal/rebulk#20

log = getLogger("MANUAL")

logging.getLogger("subliminal").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("enzyme").setLevel(logging.WARNING)
logging.getLogger("qtfaststart").setLevel(logging.CRITICAL)

log.info("Manual processor started.")

settings = None


class MediaTypes(enum.Enum):
    @classmethod
    def descriptors(cls):
        return {
            cls.MOVIE_TMDB: "Movie (via TMDB)",
            cls.MOVIE_IMDB: "Movie (via IMDB)",
            cls.TV_TMDB: "TV (via TMDB)",
            cls.TV_TVDB: "TV (via TVDB)",
            cls.TV_IMDB: "TV (via IMDB)",
            cls.CONVERT: "Convert without tagging",
            cls.SKIP: "Skip file"
        }

    def __str__(self):
        return "{0}. {1}".format(self.value, MediaTypes.descriptors().get(self, ""))

    MOVIE_TMDB = 1
    MOVIE_IMDB = 2
    TV_TMDB = 3
    TV_TVDB = 4
    TV_IMDB = 5
    CONVERT = 6
    SKIP = 7


def mediatype():
    try:
        print("Select media type:")
        for mt in MediaTypes:
            print(str(mt))
        result = raw_input("#: ")
        try:
            return MediaTypes(int(result))
        except:
            print("Invalid selection")
            return mediatype()
    except EOFError:
        print("Invalid selection")
        return mediatype()


def getValue(prompt, num=False):
    try:
        print(prompt + ":")
        value = raw_input("#: ").strip(' \"')
        # Remove escape characters in non-windows environments
        if os.name != 'nt':
            value = value.replace('\\', '')
        try:
            value = value.decode(sys.stdout.encoding)
        except:
            pass
        if num is True and value.isdigit() is False:
            print("Must be a numerical value")
            return getValue(prompt, num)
        else:
            return value
    except EOFError:
        print("Must be a numerical value")
        return getValue(prompt, num)


def getYesNo():
    yes = ['y', 'yes', 'true', '1']
    no = ['n', 'no', 'false', '0']
    try:
        data = raw_input("# [y/n]: ")
        if data.lower() in yes:
            return True
        elif data.lower() in no:
            return False
        else:
            print("Invalid selection")
            return getYesNo()
    except EOFError:
        print("Invalid selection")
        return getYesNo()


class SkipFileException(Exception):
    pass


def getInfo(fileName=None, silent=False, tag=True, tvdbId=None, tmdbId=None, imdbId=None, season=None, episode=None, language=None, original=None):
    if not tag:
        return None

    tagData = None
    # Try to guess the file is guessing is enabled
    if fileName is not None:
        tagData = guessInfo(fileName, tvdbId=tvdbId, tmdbId=tmdbId, imdbId=imdbId, season=season, episode=episode, language=language, original=original)

    if not silent:
        if tagData:
            print("Proceed using guessed identification from filename?")
            if getYesNo():
                return tagData
        else:
            print("Unable to determine identity based on filename, must enter manually")
        m_type = mediatype()
        if m_type is MediaTypes.TV_TMDB:
            tmdbId = getValue("Enter TMDB ID (TV)", True)
            season = getValue("Enter Season Number", True)
            episode = getValue("Enter Episode Number", True)
            return Metadata(MediaType.TV, tmdbId=tmdbId, season=season, episode=episode, language=language, logger=log, original=original)
        if m_type is MediaTypes.TV_TVDB:
            tvdbId = getValue("Enter TVDB ID (TV)", True)
            season = getValue("Enter Season Number", True)
            episode = getValue("Enter Episode Number", True)
            return Metadata(MediaType.TV, tvdbId=tvdbId, season=season, episode=episode, language=language, logger=log, original=original)
        if m_type is MediaTypes.TV_IMDB:
            imdbId = getValue("Enter IMDB ID (TV)", True)
            season = getValue("Enter Season Number", True)
            episode = getValue("Enter Episode Number", True)
            return Metadata(MediaType.TV, imdbId=imdbId, season=season, episode=episode, language=language, logger=log, original=original)
        elif m_type is MediaTypes.MOVIE_IMDB:
            imdbId = getValue("Enter IMDB ID (Movie)")
            return Metadata(MediaType.Movie, imdbId=imdbId, language=language, logger=log, original=original)
        elif m_type is MediaTypes.MOVIE_TMDB:
            tmdbId = getValue("Enter TMDB ID (Movie)", True)
            return Metadata(MediaType.Movie, tmdbId=tmdbId, language=language, logger=log, original=original)
        elif m_type is MediaTypes.CONVERT:
            return None
        elif m_type is MediaTypes.SKIP:
            raise SkipFileException
    else:
        if tagData and tag:
            return tagData
        else:
            return None


def guessInfo(fileName, tmdbId=None, tvdbId=None, imdbId=None, season=None, episode=None, language=None, original=None):
    guess = guessit.guessit(original or fileName)
    try:
        if guess['type'] == 'movie':
            return movieInfo(guess, tmdbId=tmdbId, imdbId=imdbId, language=language, original=original)
        elif guess['type'] == 'episode':
            return tvInfo(guess, tmdbId=tmdbId, tvdbId=tvdbId, imdbId=imdbId, season=season, episode=episode, language=language, original=original)
        else:
            return None
    except:
        log.exception("Unable to guess movie information")
        return None


def movieInfo(guessData, tmdbId=None, imdbId=None, language=None, original=None):
    if not tmdbId and not imdbId:
        tmdb.API_KEY = tmdbApiKey
        search = tmdb.Search()
        title = guessData['title']
        if 'year' in guessData:
            response = search.movie(query=title, year=guessData["year"])
            if len(search.results) < 1:
                response = search.movie(query=title, year=guessData["year"])
        else:
            response = search.movie(query=title)
        if len(search.results) < 1:
            return None
        result = search.results[0]
        release = result['release_date']
        tmdbId = result['id']
        log.debug("Guessed filename resulted in TMDB ID %s" % tmdbId)

    metadata = Metadata(MediaType.Movie, tmdbId=tmdbId, imdbId=imdbId, language=language, logger=log, original=original)
    log.info("Matched movie title as: %s %s (TMDB ID: %s)" % (metadata.title, metadata.date, metadata.tmdbId))
    return metadata


def tvInfo(guessData, tmdbId=None, tvdbId=None, imdbId=None, season=None, episode=None, language=None, original=None):
    season = season or guessData["season"]
    episode = episode or guessData["episode"]

    if not tmdbId and not tvdbId and not imdbId:
        tmdb.API_KEY = tmdbApiKey
        search = tmdb.Search()
        series = guessData["title"]
        if 'year' in guessData:
            response = search.tv(query=series, first_air_date_year=guessData["year"])
            if len(search.results) < 1:
                response = search.tv(query=series)
        else:
            response = search.tv(query=series)
        if len(search.results) < 1:
            return None
        result = search.results[0]
        tmdbId = result['id']

    metadata = Metadata(MediaType.TV, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, season=season, episode=episode, language=language, logger=log, original=original)
    log.info("Matched TV episode as %s (TMDB ID: %d) S%02dE%02d" % (metadata.showname, int(metadata.tmdbId), int(season), int(episode)))
    return metadata


def processFile(inputFile, mp, info=None, relativePath=None, silent=False, tag=True, tmdbId=None, tvdbId=None, imdbId=None, season=None, episode=None, original=None):
    # Process
    info = info or mp.isValidSource(inputFile)
    if not info:
        log.debug("Invalid file %s." % inputFile)
        return

    outputFiles = []

    # Determine original resolution
    width = info.video.video_width
    height = info.video.video_height
    if width / height > 1.4:
        if width > 6500: res = 4320
        elif 3501 <= width <= 6500: res = 2160
        elif 2001 <= width <= 3500: res = 1440
        elif 1801 <= width <= 2000: res = 1080
        elif 1001 <= width <= 1800: res = 720
        elif 701 <= width <= 1000: res = 480
        elif 451 <= width <= 700: res = 360
        else: res = 240
    else:
        if height > 4000: res = 4320
        elif 2001 <= height <= 4000: res = 2160
        elif 1301 <= height <= 2000: res = 1440
        elif 951 <= height <= 1300: res = 1080
        elif 651 <= height <= 950: res = 720
        elif 451 <= height <= 650: res = 480
        elif 300 <= height <= 450: res = 360
        else: res = 240

    resolutions = [i for i in [4320, 2160, 1440, 1080, 720, 480, 360, 240] if i <= res]

    first = True
    for resolution in resolutions:
        output = mp.process(inputFile, True, info=info, original=original, resolution=resolution)

        if output:
            language = 'eng' or mp.getDefaultAudioLanguage(output["options"]) or None
            log.debug("Tag language setting is %s, using language %s for tagging." % ('eng' or None, language))
            
            tagData = getInfo(inputFile, silent, tag=tag, tmdbId=tmdbId, tvdbId=tvdbId, imdbId=imdbId, season=season, episode=episode, language=language, original=original)
            if not tagData:
                log.info("Processing file %s" % inputFile)
            elif tagData.mediatype == MediaType.Movie:
                log.info("Processing %s" % (tagData.title))
            elif tagData.mediatype == MediaType.TV:
                log.info("Processing %s Season %02d Episode %02d - %s" % (tagData.showname, int(tagData.season), int(tagData.episode), tagData.title))

            # Tag with metadata
            tagFailed = False
            if tagData:
                try:
                    tagData.writeTags(output['output'], mp.converter, True, False, width=output['x'], height=output['y'])
                except:
                    log.exception("There was an error tagging the file")
                    tagFailed = True

            # QTFS
            if not tagFailed:
                mp.QTFS(output['output'])

            # Permissions
            mp.setPermissions(output['output'])

            # Complete initial file
            if first == True:
                origInputFile = inputFile
                inputFile = str(output['output']).replace('.mp4', '-copy.mp4')
                shutil.copy(output['output'], inputFile)
                log.debug("%s copied to %s." % (output['output'], inputFile))
                info = mp.isValidSource(inputFile)
                first = False

                # Reverse Ouput
                output['output'] = mp.restoreFromOutput(origInputFile, output['output'], resolution=resolution)
            else:
                # Reverse Ouput
                output['output'] = mp.restoreFromOutput(inputFile, output['output'], resolution=resolution)

            # Move file to correct location
            outputFiles += mp.moveFile(output['output'])
        else:
            log.error("There was an error processing file %s, no output data received" % inputFile)

    if os.path.isfile(origInputFile):
        log.debug("%s exists, deleting copied file." % (origInputFile))
        if mp.removeFile(origInputFile):
            log.debug("%s deleted." % origInputFile)
        else:
            log.error("Couldn't delete %s." % origInputFile)

    if os.path.isfile(inputFile):
        log.debug("%s exists, deleting copied file." % (inputFile))
        if mp.removeFile(inputFile):
            log.debug("%s deleted." % inputFile)
        else:
            log.error("Couldn't delete %s." % inputFile)

    for file in outputFiles:
        mp.setPermissions(file)

    # Run any post process scripts
    if settings.postprocess:
        postprocessor = PostProcessor(output_files, wait=settings.waitpostprocess)
        if tagData:
            if tagData.mediatype == MediaType.Movie:
                postprocessor.setMovie(tagData.tmdbId)
            elif tagData.mediatype == MediaType.TV:
                postprocessor.setTV(tagData.tmdbId, tagData.season, tagData.episode)
        postprocessor.run_scripts()


def walkDir(dir, silent=False, preserveRelative=False, tmdbId=None, imdbId=None, tvdbId=None, tag=True, optionsOnly=False):
    files = []
    mp = MediaProcessor(settings, logger=log)
    for r, d, f in os.walk(dir):
        for file in f:
            files.append(os.path.join(r, file))
    for filepath in files:
        info = mp.isValidSource(filepath)
        if info:
            log.info("Processing file %s" % (filepath))
            relative = os.path.split(os.path.relpath(filepath, dir))[0] if preserveRelative else None
            if optionsOnly:
                displayOptions(filepath)
                continue
            try:
                processFile(filepath, mp, info=info, relativePath=relative, silent=silent, tag=tag, tmdbId=tmdbId, tvdbId=tvdbId, imdbId=imdbId)
            except SkipFileException:
                log.debug("Skipping file %s." % filepath)


def displayOptions(path):
    mp = MediaProcessor(settings)
    log.info(mp.jsonDump(path))


def showCodecs():
    data = {
        'video': video_codec_list,
        'audio': audio_codec_list,
        'subtitle': subtitle_codec_list,
        'attachment': attachment_codec_list
    }
    print("List of supported codecs within MMT")
    print("Format:")
    print("  [MMT Codec]: [FFMPEG Encoder]")
    for key in data:
        print("=============")
        print(" " + key)
        print("=============")
        for codec in data[key]:
            print("%s: %s" % (codec.codec_name, codec.ffmpeg_codec_name))


def main():
    global settings

    parser = argparse.ArgumentParser(description="Manual conversion and tagging script for transcoder")
    parser.add_argument('-i', '--input', help='The source that will be converted. May be a file or a directory')
    parser.add_argument('-c', '--config', help='Specify an alternate configuration file location')
    parser.add_argument('-a', '--auto', action="store_true", help="Enable auto mode, the script will not prompt you for any further input, good for batch files. It will guess the metadata using guessit")
    parser.add_argument('-s', '--season', help="Specifiy the season number")
    parser.add_argument('-e', '--episode', help="Specify the episode number")
    parser.add_argument('-tvdb', '--tvdbid', help="Specify the TVDB ID for media")
    parser.add_argument('-imdb', '--imdbid', help="Specify the IMDB ID for media")
    parser.add_argument('-tmdb', '--tmdbid', help="Specify the TMDB ID for media")
    parser.add_argument('-nm', '--nomove', action='store_true', help="Overrides and disables the custom moving of file options that come from outputDir and move-to")
    parser.add_argument('-nd', '--nodelete', action='store_true', help="Overrides and disables deleting of original files")
    parser.add_argument('-np', '--nopost', action="store_true", help="Overrides and disables the execution of additional post processing scripts")
    parser.add_argument('-pr', '--preserverelative', action='store_true', help="Preserves relative directories when processing multiple files using the copy-to or move-to functionality")
    parser.add_argument('-m', '--moveto', help="Override move-to value setting in autoProcess.ini changing the final destination of the file")
    parser.add_argument('-oo', '--optionsonly', action="store_true", help="Display generated conversion options only, do not perform conversion")
    parser.add_argument('-cl', '--codeclist', action="store_true", help="Print a list of supported codecs and their paired FFMPEG encoders")
    parser.add_argument('-o', '--original', help="Specify the original source/release filename")

    args = vars(parser.parse_args())

    # Setup the silent mode
    silent = args['auto']

    print("Python %s-bit %s." % (struct.calcsize("P") * 8, sys.version))
    print("Guessit version: %s." % guessit.__version__)

    if args['codeclist']:
        showCodecs()
        return

    # Settings overrides
    if args['config'] and os.path.exists(args['config']):
        settings = ReadSettings(args['config'], logger=log)
    elif args['config'] and os.path.exists(os.path.join(os.path.dirname(sys.argv[0]), args['config'])):
        settings = ReadSettings(os.path.join(os.path.dirname(sys.argv[0]), args['config']), logger=log)
    else:
        settings = ReadSettings(logger=log)
    if (args['nomove']):
        settings.outputDir = None
        settings.moveTo = None
        print("No-move enabled")
    elif (args['moveto']):
        settings.moveTo = args['moveto']
        print("Overriden move-to to " + args['moveto'])
    if (args['nodelete']):
        settings.delete = False
        print("No-delete enabled")
    if (args['nopost']):
        settings.postprocess = False
        print("No post processing enabled")
    if (args['optionsonly']):
        logging.getLogger("resources.mediaprocessor").setLevel(logging.CRITICAL)
        print("Options only mode enabled")

    # Establish the path we will be working with
    if (args['input']):
        path = (str(args['input']))
        try:
            path = glob.glob(path)[0]
        except:
            pass
    else:
        path = getValue("Enter path to file")

    if os.path.isdir(path):
        walkDir(path, silent=silent, tmdbId=args.get('tmdbid'), tvdbId=args.get('tvdbid'), imdbId=args.get('imdbid'), preserveRelative=args['preserverelative'], tag=True, optionsOnly=args['optionsonly'])
    elif (os.path.isfile(path)):
        mp = MediaProcessor(settings, logger=log)
        info = mp.isValidSource(path)
        if info:
            if (args['optionsonly']):
                displayOptions(path)
                return
            try:
                processFile(path, mp, info=info, silent=silent, tag=True, tmdbId=args.get('tmdbid'), tvdbId=args.get('tvdbid'), imdbId=args.get('imdbid'), season=args.get('season'), episode=args.get('episode'), original=args.get('original'))
            except SkipFileException:
                log.debug("Skipping file %s" % path)

        else:
            print("File %s is not in a valid format" % (path))
    else:
        print("File %s does not exist" % (path))


if __name__ == '__main__':
    main()
