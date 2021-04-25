from __future__ import unicode_literals
import os
import time
import json
import sys
import shutil
import logging
import re
from converter import Converter, FFMpegConvertError, ConverterError
from converter.avcodecs import BaseCodec
from resources.extensions import subtitleCodecExtensions
from resources.metadata import Metadata
from resources.postprocess import PostProcessor
from resources.lang import getAlpha3TCode
from autoprocess import plex
try:
    from babelfish import Language
except:
    pass
try:
    import subliminal
except:
    pass
try:
    from pymediainfo import MediaInfo
except:
    MediaInfo = None


class MediaProcessor:
    deleteSubs = set()

    def __init__(self, settings, logger=None):
        self.log = logger or logging.getLogger(__name__)
        self.settings = settings
        self.converter = Converter(settings.ffmpeg, settings.ffprobe)

    def fullprocess(self, inputFile, mediatype, reportProgress=False, original=None, info=None, tmdbId=None, tvdbId=None, imdbId=None, season=None, episode=None, language=None):
        try:
            info = self.isValidSource(inputFile)
            if info:
                self.log.info("Processing %s." % inputFile)
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
                    output = self.process(inputFile, original=original, info=info, resolution=resolution)

                    if output:
                        if not language:
                            language = 'eng' or self.getDefaultAudioLanguage(output['options']) or None
                        self.log.debug("Tag language setting is %s, using language %s for tagging." % ('eng' or None, language))
                        # Tag with metadata
                        tagFailed = False
                        try:
                            tag = Metadata(mediatype, tvdbId=tvdbId, tmdbId=tmdbId, imdbId=imdbId, season=season, episode=episode, original=original, language=language)
                            tmdbId = tag.tmdbId
                            self.log.info("Tagging %s with TMDB ID %s." % (output['output'], tag.tmdbId))
                            tag.writeTags(output['output'], self.converter, True, False, output['x'], output['y'])
                        except:
                            self.log.exception("Unable to tag file")
                            tagFailed = True

                        # QTFS
                        if not tagFailed:
                            self.QTFS(output['output'])

                        # Permissions
                        self.setPermissions(output['output'])

                        # Complete initial file
                        if first == True:
                            origInputFile = inputFile
                            inputFile = str(output['output']).replace('.mp4', '-copy.mp4')
                            shutil.copy(output['output'], inputFile)
                            self.log.debug("%s copied to %s." % (output['output'], inputFile))
                            info = self.isValidSource(inputFile)
                            first = False

                        # Move to Radarr/Sonarr expected output dir
                        if not self.settings.moveTo:
                            output['output'] = self.restoreFromOutput(origInputFile, output['output'], resolution=resolution)

                        # Move file to correct location
                        outputFiles += self.moveFile(output['output'])

                        # Refresh Plex
                        if self.settings.Plex.get('refresh', False):
                            try:
                                plex.refreshPlex(self.settings, mediatype, self.log)
                            except:
                                self.log.exception("Error refreshing Plex.")

                if os.path.isfile(origInputFile):
                    self.log.debug("%s exists, deleting copied file." % (origInputFile))
                    if self.removeFile(origInputFile):
                        self.log.debug("%s deleted." % origInputFile)
                    else:
                        self.log.error("Couldn't delete %s." % origInputFile)

                if os.path.isfile(inputFile):
                    self.log.debug("%s exists, deleting copied file." % (inputFile))
                    if self.removeFile(inputFile):
                        self.log.debug("%s deleted." % inputFile)
                    else:
                        self.log.error("Couldn't delete %s." % inputFile)

                for file in outputFiles:
                    self.setPermissions(file)

                # Run any post process scripts
                if self.settings.postprocess:
                    postprocessor = PostProcessor(outputFiles, self.log, wait=self.settings.waitpostprocess)
                    postprocessor.setEnv(mediatype, tmdbId, season, episode)
                    postprocessor.run_scripts()

                return outputFiles
            else:
                self.log.info("File %s is not valid" % inputFile)
        except:
            self.log.exception("Error processing")
        return False

    # Process a file from start to finish, with checking to make sure formats are compatible with selected settings
    def process(self, inputFile, reportProgress=False, original=None, info=None, progressOutput=None, resolution=None):
        self.log.debug("Process started.")

        delete = self.settings.delete if resolution == None or resolution == 240 else False
        deleted = False
        options = None
        preopts = None
        postopts = None
        outputFile = None
        rippedSubs = []
        downloadedSubs = []

        info = info or self.isValidSource(inputFile)

        if info:
            try:
                options, preopts, postopts, ripSubOpts, downloadedSubs = self.generateOptions(inputFile, info=info, original=original, resolution=resolution)
            except:
                self.log.exception("Unable to generate options, unexpected exception occurred.")
                return None
            if not options:
                self.log.error("Error converting, inputFile %s had a valid extension but returned no data. Either the file does not exist, was unreadable, or was an incorrect format." % inputFile)
                return None

            try:
                self.log.info("Output Data")
                self.log.info(json.dumps(options, sort_keys=False, indent=4))
                self.log.info("Preopts")
                self.log.info(json.dumps(preopts, sort_keys=False, indent=4))
                self.log.info("Postopts")
                self.log.info(json.dumps(postopts, sort_keys=False, indent=4))
                self.log.info("Downloaded Subtitles")
                self.log.info(json.dumps(downloadedSubs, sort_keys=False, indent=4))

            except:
                self.log.exception("Unable to log options.")

            rippedSubs = self.ripSubs(inputFile, ripSubOpts)
            try:
                outputFile, inputFile = self.convert(options, preopts, postopts, reportProgress, progressOutput, resolution=resolution)
            except:
                self.log.exception("Unexpected exception encountered during conversion")
                return None

            if not outputFile:
                self.log.debug("Error converting, no outputFile generated for inputFile %s." % inputFile)
                return None

            self.log.debug("%s created from %s successfully." % (outputFile, inputFile))

            if outputFile == inputFile:
                if self.settings.outputDir:
                    try:
                        outputFile = os.path.join(self.settings.outputDir, os.path.split(inputFile)[1])
                        self.log.debug("Outputfile set to %s." % outputFile)
                        shutil.copy(inputFile, outputFile)
                    except:
                        self.log.exception("Error moving file to output directory.")
                        delete = False
                else:
                    delete = False

            if delete:
                self.log.debug("Attempting to remove %s." % inputFile)
                if self.removeFile(inputFile):
                    self.log.debug("%s deleted." % inputFile)
                    deleted = True
                else:
                    self.log.error("Couldn't delete %s." % inputFile)

                for subfile in self.deleteSubs:
                    self.log.debug("Attempting to remove subtitle %s." % subfile)
                    if self.removeFile(subfile):
                        self.log.debug("Subtitle %s deleted." % subfile)
                    else:
                        self.log.debug("Unable to delete subtitle %s." % subfile)
                self.deleteSubs = set()

            dim = self.getDimensions(outputFile)
            inputExtension = self.parseFile(inputFile)[2]
            outputExtension = self.parseFile(outputFile)[2]

            return {'input': inputFile,
                    'inputExtension': inputExtension,
                    'inputDeleted': deleted,
                    'output': outputFile,
                    'outputExtension': outputExtension,
                    'options': options,
                    'preopts': preopts,
                    'postopts': postopts,
                    'external_subs': downloadedSubs + rippedSubs,
                    'x': dim['x'],
                    'y': dim['y']}
        return None

    def videoStreamTitle(self, width=0, height=0, swidth=0, sheight=0):
        output = "Video"

        if not width and not height:
            width = swidth
            height = sheight

        if width >= 7600 or height >= 4300:
            output = "4320p (8K)"
        elif width >= 3800 or height >= 2100:
            output = "2160p (4K)"
        elif width >= 2530 or height >= 1400:
            output = "1440p (2K)"
        elif width >= 1900 or height >= 1060:
            output = "1080p"
        elif width >= 1260 or height >= 700:
            output = "720p"
        elif width >= 834 or height >= 460:
            output = "480p"
        elif width >= 620 or height >= 220:
            output = "360p"
        else:
            output = "240p"

        return output.strip() if output else None

    def audioStreamTitle(self, channels, disposition):
        output = "Audio"
        if channels == 1:
            output = "Mono"
        elif channels == 2:
            output = "Stereo"
        elif channels > 2:
            output = "%d.1 Channel" % (channels - 1)

        if disposition.get("comment"):
            output += " (Commentary)"
        if disposition.get("hearing_impaired"):
            output += " (Hearing Impaired)"
        if disposition.get("visual_impaired"):
            output += " (Visual Impaired)"
        if disposition.get("dub"):
            output += " (Dub)"
        return output.strip() if output else None

    def subtitleStreamTitle(self, disposition):
        output = ""
        if disposition.get("forced"):
            output += "Forced "
        if disposition.get("hearing_impaired"):
            output += "Hearing Impaired "
        if disposition.get("comment"):
            output += "Commentary "
        if disposition.get("visual_impaired"):
            output += "Visual Impaired "
        if disposition.get("dub"):
            output += "Dub "
        return output.strip() if output else None

    # Determine if a file can be read by FFPROBE
    def isValidSource(self, inputFile):
        try:
            extension = self.parseFile(inputFile)[2]
            if extension in self.settings.ignoredExtensions:
                self.log.debug("Invalid source, extension is blacklisted [ignored-extensions].")
                return None
            if os.path.getsize(inputFile) < 95000000:
                self.log.debug("Invalid source, below minimum size threshold [minimum-size].")
                return None
            info = self.converter.probe(inputFile)
            if not info:
                self.log.debug("Invalid source, no data returned.")
                return None
            if not info.video:
                self.log.debug("Invalid source, no video stream detected.")
                return None
            if not info.audio or len(info.audio) < 1:
                self.log.debug("Invalid source, no audio stream detected.")
                return None
            if MediaInfo:
                try:
                    media_info = MediaInfo.parse(inputFile)
                    for track in media_info.tracks:
                        if track.title and track.streamorder is not None:
                            so = int(track.streamorder)
                            stream = info.streams[so]
                            stream.metadata['title'] = track.title
                except:
                    self.log.exception("Pymediainfo exception.")
                    pass
            return info
        except:
            self.log.exception("isValidSource unexpectedly threw an exception, returning None.")
            return None

    def isValidSubtitleSource(self, inputFile):
        try:
            info = self.converter.probe(inputFile)
            if info:
                if len(info.subtitle) < 1 or info.video or len(info.audio) > 0:
                    return None
            return info
        except:
            self.log.exception("isValidSubtitleSource unexpectedly threw an exception, returning None.")
            return None

    def getDefaultAudioLanguage(self, options):
        for a in options.get("audio", []):
            if "+default" in a.get("disposition", "").lower():
                self.log.debug("Default audio language is %s." % a.get("language"))
                return a.get("language")

    # Get values for width and height to be passed to the tagging classes for proper HD tags
    def getDimensions(self, inputFile):
        info = self.converter.probe(inputFile)

        if info:
            self.log.debug("Height: %s" % info.video.video_height)
            self.log.debug("Width: %s" % info.video.video_width)

            return {'y': info.video.video_height,
                    'x': info.video.video_width}

        return {'y': 0,
                'x': 0}

    # Generate a JSON formatter dataset with the input and output information and ffmpeg command for a theoretical conversion
    def jsonDump(self, inputFile, original=None, resolution=None):
        dump = {}
        dump["input"], info = self.generateSourceDict(inputFile)
        dump["output"], dump["preopts"], dump["postopts"], dump["ripSubOpts"], dump["downloadedsubs"] = self.generateOptions(inputFile, info=info, original=original)
        parsed = self.converter.parse_options(dump["output"])
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        outputFile, outputExtension = self.getOutputFile(inputDir, filename, inputExtension, resolution=resolution)
        cmds = self.converter.ffmpeg.generateCommands(outputFile, parsed, dump["preopts"], dump["postopts"])
        dump["ffmpeg_commands"] = []
        dump["ffmpeg_commands"].append(" ".join("\"%s\"" % item if " " in item and "\"" not in item else item for item in cmds))
        for suboptions in dump["ripSubOpts"]:
            subparsed = self.converter.parse_options(suboptions)
            extension = self.getSubExtensionFromCodec(suboptions['format'])
            subOutputFile = self.getSubOutputFileFromOptions(inputFile, suboptions, extension)
            subcmds = self.converter.ffmpeg.generateCommands(subOutputFile, subparsed)
            dump["ffmpeg_commands"].append(" ".join(str(item) for item in subcmds))
        for sub in dump["downloadedsubs"]:
            self.log.debug("Cleaning up downloaded sub %s which was only used to simulate options." % (sub))
            self.removeFile(sub)

        return json.dumps(dump, sort_keys=False, indent=4).replace("\\\\", "\\").replace("\\\"", "\"")

    # Generate a dict of data about a source file
    def generateSourceDict(self, inputFile):
        output = {}
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        output['extension'] = inputExtension
        probe = self.isValidSource(inputFile)
        # probe = self.converter.probe(inputFile)
        self.titleDispositionCheck(probe)
        if probe:
            output.update(probe.json)
        else:
            output['error'] = "Invalid input, unable to read"
        return output, probe

    # Pass over audio and subtitle streams to ensure the language properties are safe, return any adjustments made to SWL/AWL if relax is enabled
    def safeLanguage(self, info):
        awl = ['eng']
        swl = ['eng']
        overrideLang = (len(awl) > 0)

        # Loop through audio streams and clean up language metadata by standardizing undefined languages and applying the ADL setting
        for a in info.audio:
            a.metadata['language'] = getAlpha3TCode(a.metadata.get('language'), 'eng')
            if len(awl) > 0 and a.metadata.get('language') in awl:
                overrideLang = False

        if overrideLang:
            awl = []
            self.log.info("No audio streams detected in any appropriate language, relaxing restrictions [allow-audio-language-relax].")

        # Prep subtitle streams by cleaning up languages and setting SDL
        for s in info.subtitle:
            s.metadata['language'] = getAlpha3TCode(s.metadata.get('language'), 'eng')
        return awl, swl

    # Check and see if clues about the disposition are in the title
    def titleDispositionCheck(self, info):
        for stream in info.streams:
            title = stream.metadata.get('title', '').lower()
            if 'comment' in title:
                self.log.debug("Found comment in stream title, setting comment disposition to True.")
                stream.disposition['comment'] = True
            if 'hearing' in title:
                self.log.debug("Found hearing in stream title, setting hearing_impaired disposition to True.")
                stream.disposition['hearing_impaired'] = True
            if 'visual' in title:
                self.log.debug("Found visual in stream title, setting visual_impaired disposition to True.")
                stream.disposition['visual_impaired'] = True
            if 'forced' in title:
                self.log.debug("Found foced in stream title, setting forced disposition to True.")
                stream.disposition['forced'] = True

    # Generate a dict of options to be passed to FFMPEG based on selected settings and the source file parameters and streams
    def generateOptions(self, inputFile, info=None, original=None, resolution=None):
        # Get path information from the input file
        sources = [inputFile]
        ripSubOpts = []

        info = info or self.converter.probe(inputFile)

        if not info:
            self.log.error("FFPROBE returned no value for inputFile %s (exists: %s), either the file does not exist or is not a format FFPROBE can read." % (inputFile, os.path.exists(inputFile)))
            return None, None, None, None

        awl, swl = self.safeLanguage(info)
        self.titleDispositionCheck(info)

        try:
            self.log.info("Input Data")
            self.log.info(json.dumps(info.json, sort_keys=False, indent=4))
        except:
            self.log.exception("Unable to print input file data")

        # Video stream
        self.log.info("Reading video stream.")
        self.log.info("Video codec detected: %s." % info.video.codec)
        self.log.info("Pix Fmt: %s." % info.video.pix_fmt)
        self.log.info("Profile: %s." % info.video.profile)

        vdebug = "video"

        width = info.video.video_width
        height = info.video.video_height
        if width / height > 1.4:
            normal = True
        else:
            normal = False
        vcodec = "h264"

        vcrf = 22
        vpreset = 'veryfast'

        if resolution == 4320:
            vwidth = 7680 if normal == True else 5760
            vbitrate = 28600
            vmaxrate = '96640k'
            vbufsize = '144500k'
            vprofile = 'high'
            vpix_fmt = 'yuv420p10le'
            audioBitrate = 512
        elif resolution == 2160:
            vwidth = 3840 if normal == True else 2880
            vbitrate = 16100
            vmaxrate = '48512k'
            vbufsize = '72500k'
            vprofile = 'high'
            vpix_fmt = 'yuv420p10le'
            audioBitrate = 512
        elif resolution == 1440:
            vwidth = 2560 if normal == True else 1920
            vbitrate = 9000
            vmaxrate = '19968k'
            vbufsize = '29900k'
            vprofile = 'high'
            vpix_fmt = 'yuv420p10le'
            audioBitrate = 384
        elif resolution == 1080:
            vwidth = 1920 if normal == True else 1440
            vbitrate = 4900
            vmaxrate = '9856k'
            vbufsize = '14500k'
            vprofile = 'high'
            vpix_fmt = 'yuv420p'
            audioBitrate = 256
        elif resolution == 720:
            vwidth = 1280 if normal == True else 960
            vbitrate = 2850
            vmaxrate = '6336k'
            vbufsize = '9500k'
            vprofile = 'high'
            vpix_fmt = 'yuv420p'
            audioBitrate = 192
        elif resolution == 480:
            vwidth = 854 if normal == True else 640
            vbitrate = 1425
            vmaxrate = '3432k'
            vbufsize = '3500k'
            vprofile = 'main'
            vpix_fmt = 'yuv420p'
            audioBitrate = 128
        elif resolution == 360:
            vwidth = 640 if normal == True else 480
            vbitrate = 800
            vmaxrate = '928k'
            vbufsize = '1300k'
            vprofile = 'baseline'
            vpix_fmt = 'yuv420p'
            audioBitrate = 96
        elif resolution == 240:
            vwidth = 426 if normal == True else 320
            vbitrate = 500
            vmaxrate = '652k'
            vbufsize = '950k'
            vprofile = 'baseline'
            vpix_fmt = 'yuv420p'
            audioBitrate = 64
        else:
            vwidth = None
            vbitrate = info.format.bitrate / 1000
            vmaxrate = None
            vbufsize = None
            vprofile = 'high'
            vpix_fmt = 'yuv420p'

        vlevel = 0.0
        vfieldorder = info.video.field_order

        self.log.debug("Video codec: %s." % vcodec)
        self.log.debug("Video bitrate: %s." % vbitrate)
        self.log.debug("Video CRF: %s." % vcrf)
        self.log.debug("Video maxrate: %s." % vmaxrate)
        self.log.debug("Video bufsize: %s." % vbufsize)
        self.log.debug("Video level: %s." % vlevel)
        self.log.debug("Video profile: %s." % vprofile)
        self.log.debug("Video preset: %s." % vpreset)
        self.log.debug("Video pix format: %s." % vpix_fmt)
        self.log.debug("Video field order: %s." % vfieldorder)
        self.log.debug("Video width: %s." % vwidth)
        self.log.debug("Video debug %s." % vdebug)
        self.log.info("Creating %s video stream from source stream %d." % (vcodec, info.video.index))

        video_settings = {
            'codec': vcodec,
            'map': info.video.index,
            'bitrate': vbitrate,
            'crf': vcrf,
            'maxrate': vmaxrate,
            'bufsize': vbufsize,
            'level': vlevel,
            'profile': vprofile,
            'tune': 'zerolatency',
            'preset': vpreset,
            'pix_fmt': vpix_fmt,
            'field_order': vfieldorder,
            'width': vwidth,
            'title': self.videoStreamTitle(width=vwidth, swidth=info.video.video_width, sheight=info.video.video_height),
            'debug': vdebug,
        }

        # Audio streams
        self.log.info("Reading audio streams.")

        # Iterate through audio streams
        audio_settings = []
        blocked_audio_languages = []

        # Sort incoming streams so that things like first language preferences respect these options
        audio_streams = info.audio
        try:
            self.sortStreams(audio_streams, awl)
        except:
            self.log.exception("Error sorting source audio streams [sort-streams].")

        for a in audio_streams:
            self.log.info("Audio detected for stream %s - %s %s %d channel." % (a.index, a.codec, a.metadata['language'], a.audio_channels))

            if a.codec == 'truehd':
                if len([x for x in info.audio if x.audio_channels == a.audio_channels and x.metadata['language'] == a.metadata['language']]) > 1:
                    self.log.info("Skipping trueHD stream %s as typically the 2nd audio stream is the AC3 core of the truehd stream [audio-ignore-truehd]." % a.index)
                    continue
                else:
                    self.log.info("TrueHD stream detected but no other comparable audio streams in source, cannot skip stream %s [audio-ignore-truehd]." % a.index)

            # Proceed if no whitelist is set, or if the language is in the whitelist
            uadata = None
            if self.validLanguage(a.metadata['language'], awl, blocked_audio_languages):
                # Create friendly audio stream if the default audio stream has too many channels
                if a.audio_channels > 2 and audioBitrate > 128:
                    ua_bitrate = audioBitrate
                    ua_disposition = a.dispostr
                    ua_codec = 'aac'

                    self.log.debug("Audio codec: %s." % ua_codec)
                    self.log.debug("Channels: 2.")
                    self.log.debug("Bitrate: %s." % ua_bitrate)
                    self.log.debug("Language: %s." % a.metadata['language'])
                    self.log.debug("Disposition: %s." % ua_disposition)

                    uadata = {
                        'map': a.index,
                        'codec': ua_codec,
                        'channels': 2,
                        'bitrate': ua_bitrate,
                        'samplerate': 48000,
                        'language': a.metadata['language'],
                        'disposition': ua_disposition,
                        'title': self.audioStreamTitle(2, a.disposition),
                        'debug': 'universal-audio'
                    }

                adebug = "audio"
                # If the universal audio option is enabled and the source audio channel is only stereo, the additional universal stream will be skipped and a single channel will be made regardless of codec preference to avoid multiple stereo channels
                adisposition = a.dispostr
                # If desired codec is the same as the source codec, copy to avoid quality loss
                acodec = 'copy' if a.codec == 'aac' else 'aac'
                if a.audio_channels <= 2 or audioBitrate <= 128:
                    self.log.debug("Overriding default channel settings because universal audio is enabled but the source is stereo [universal-audio].")
                    audio_channels = 2
                    abitrate = audioBitrate
                    adebug = "universal-audio"
                    acodec = 'aac'
                else:
                    # Audio channel adjustments
                    if a.audio_channels > 6:
                        self.log.debug("Audio source exceeds maximum channels, can not be copied. Settings channels to 6 [audio-max-channels].")
                        adebug = adebug + ".max-channels"
                        audio_channels = 6
                        acodec = 'aac'
                        abitrate = 3 * audioBitrate
                    else:
                        audio_channels = a.audio_channels
                        abitrate = (a.audio_channels / 2) * audioBitrate
                        acodec = 'aac'

                self.log.debug("Audio codec: %s." % acodec)
                self.log.debug("Channels: %s." % audio_channels)
                self.log.debug("Bitrate: %s." % abitrate)
                self.log.debug("Language: %s." % a.metadata['language'])
                self.log.debug("Disposition: %s." % adisposition)
                self.log.debug("Debug: %s." % adebug)

                if len(audio_settings) >= 1 and audioBitrate <= 128:
                    self.log.info("Ignoring %s audio stream from source stream %d." % (acodec, a.index))
                elif len(audio_settings) >= 2 and audio_channels <= 2:
                    self.log.info("Ignoring duplicate %s audio stream from source stream %d." % (acodec, a.index))
                else:
                    self.log.info("Creating %s audio stream from source stream %d." % (acodec, a.index))
                    audio_settings.append({
                        'map': a.index,
                        'codec': acodec,
                        'channels': audio_channels,
                        'bitrate': abitrate,
                        'samplerate': 48000 if audioBitrate <= 256 else 96000,
                        'language': a.metadata['language'],
                        'disposition': adisposition,
                        'title': self.audioStreamTitle(audio_channels, a.disposition),
                        'debug': adebug
                    })

                # Add the universal audio stream last instead
                if uadata:
                    self.log.info("Creating %s audio stream from source audio stream %d [universal-audio]." % (uadata.get('codec'), a.index))
                    audio_settings.append(uadata)

        # Set Default Audio Stream
        try:
            self.setDefaultAudioStream(audio_settings)
        except:
            self.log.exception("Unable to set the default audio stream.")

        # Iterate through subtitle streams
        subtitle_settings = []
        blocked_subtitle_languages = []
        self.log.info("Reading subtitle streams.")
        for s in info.subtitle:
            try:
                image_based = self.isImageBasedSubtitle(inputFile, s.index)
            except:
                self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
                continue
            self.log.info("%s-based subtitle detected for stream %s - %s %s." % ("Image" if image_based else "Text", s.index, s.codec, s.metadata['language']))

            scodec = None
            sdisposition = s.dispostr
            if not image_based:
                scodec = 'copy' if s.codec == 'mov_text' else 'mov_text'

            if scodec:
                # Proceed if no whitelist is set, or if the language is in the whitelist
                if self.validLanguage(s.metadata['language'], swl, blocked_subtitle_languages):
                    self.log.info("Creating %s subtitle stream from source stream %d." % (scodec, s.index))
                    subtitle_settings.append({
                        'map': s.index,
                        'codec': scodec,
                        'language': s.metadata['language'],
                        'disposition': sdisposition,
                        'title': self.subtitleStreamTitle(s.disposition),
                        'debug': 'subtitle.embed-subs'
                    })
            else:
                if self.validLanguage(s.metadata['language'], swl, blocked_subtitle_languages):
                    if scodec:
                        ripSub = [{
                            'map': s.index,
                            'codec': scodec,
                            'language': s.metadata['language'],
                            'debug': "subtitle"
                        }]
                        options = {
                            'source': [inputFile],
                            'subtitle': ripSub,
                            'format': s.codec if scodec == 'copy' else scodec,
                            'disposition': s.dispostr,
                            'language': s.metadata['language'],
                            'index': s.index
                        }
                        ripSubOpts.append(options)

        # Attempt to download subtitles if missing using subliminal
        downloadedSubs = []
        try:
            downloadedSubs = self.downloadSubtitles(inputFile, info.subtitle, swl, original)
        except:
            self.log.exception("Unable to download subtitles [download-subs].")

        # External subtitle import
        valid_external_subs = None
        valid_external_subs = self.scanForExternalSubs(inputFile, swl)
        for external_sub in valid_external_subs:
            try:
                image_based = self.isImageBasedSubtitle(external_sub.path, 0)
            except:
                self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
                continue
            scodec = None if image_based else 'mov_text'
            sdisposition = external_sub.subtitle[0].dispostr

            if not scodec:
                self.log.info("Skipping external subtitle file %s, no appropriate codecs found or embed disabled." % os.path.basename(external_sub.path))
                continue
            if self.validLanguage(external_sub.subtitle[0].metadata['language'], swl, blocked_subtitle_languages):
                if external_sub.path not in sources:
                    sources.append(external_sub.path)

                self.log.info("Creating %s subtitle stream by importing %s-based %s [embed-subs]." % (scodec, "Image" if image_based else "Text", os.path.basename(external_sub.path)))
                subtitle_settings.append({
                    'source': sources.index(external_sub.path),
                    'map': 0,
                    'codec': scodec,
                    'disposition': sdisposition,
                    'title': self.subtitleStreamTitle(external_sub.subtitle[0].disposition),
                    'language': external_sub.subtitle[0].metadata['language'],
                    'debug': 'subtitle.embed-subs'})

                self.log.debug("Path: %s." % external_sub.path)
                self.log.debug("Codec: %s." % scodec)
                self.log.debug("Langauge: %s." % external_sub.subtitle[0].metadata['language'])
                self.log.debug("Disposition: %s." % sdisposition)

                self.deleteSubs.add(external_sub.path)

        # Set Default Subtitle Stream
        try:
            self.setDefaultSubtitleStream(subtitle_settings)
        except:
            self.log.exception("Unable to set the default subtitle stream.")

        # Sort Options
        try:
            self.sortStreams(subtitle_settings, swl)
        except:
            self.log.exception("Error sorting output stream options [sort-streams].")

        # Attachments
        attachments = []
        for f in info.attachment:
            if f.codec in self.settings.attachmentcodec and 'mimetype' in f.metadata and 'filename' in f.metadata:
                attachment = {
                    'map': f.index,
                    'codec': 'copy',
                    'filename': f.metadata['filename'],
                    'mimetype': f.metadata['mimetype']
                }
                attachments.append(attachment)

        # Collect all options
        options = {
            'source': sources,
            'format': 'mp4',
            'video': video_settings,
            'audio': audio_settings,
            'subtitle': subtitle_settings,
            'attachment': attachments
        }

        preopts =  ['-hide_banner']
        postopts = ['-threads', str(self.settings.threads), '-metadata:g', 'encoding_tool=MMT', '-deinterlace', '-vsync', '1', '-g', '60', '-sc_threshold', '0', '-movflags', 'faststart']

        # FFMPEG allows TrueHD experimental
        if options.get('format') in ['mp4']:
            for a in options['audio']:
                if info.streams[a.get('map')].codec == 'truehd' and a.get('codec') == 'copy':
                    self.log.debug("Adding experimental flag for mp4 with trueHD as a trueHD stream is being copied.")
                    postopts.extend(['-strict', 'experimental'])
                    break

        if len(options['subtitle']) > 0:
            self.log.debug("Subtitle streams detected, adding fix_sub_duration option to preopts.")
            preopts.append('-fix_sub_duration')

        if vcodec != 'copy':
            try:
                opts, device = self.setAcceleration(info.video.codec)
                preopts.extend(opts)
                for k in self.settings.hwdevices:
                    if k in vcodec:
                        match = self.settings.hwdevices[k]
                        self.log.debug("Found a matching device %s for encoder %s [hwdevices]." % (match, vcodec))
                        if not device:
                            self.log.debug("No device was set by the decoder, setting device to %s for encoder %s [hwdevices]." % (match, vcodec))
                            preopts.extend(['-init_hw_device', '%s=mmt:%s' % (k, match)])
                            options['video']['device'] = "mmt"
                        elif device == match:
                            self.log.debug("Device was already set by the decoder, using same device %s for encoder %s [hwdevices]." % (device, vcodec))
                            options['video']['device'] = "mmt"
                        else:
                            self.log.debug("Device was already set by the decoder but does not match encoder, using secondary device %s for encoder %s [hwdevices]." % (match, vcodec))
                            preopts.extend(['-init_hw_device', '%s=mmt2:%s' % (k, match)])
                            options['video']['device'] = "mmt2"
                            options['video']['decode_device'] = "mmt"
                        break
            except:
                self.log.exception("Error when trying to determine hardware acceleration support.")

        # HEVC Tagging for copied streams
        if info.video.codec in ['x265', 'h265', 'hevc'] and vcodec == 'copy':
            postopts.extend(['-tag:v', 'hvc1'])
            self.log.info("Tagging copied video stream as hvc1")

        return options, preopts, postopts, ripSubOpts, downloadedSubs

    def validLanguage(self, language, whitelist, blocked=[]):
        return ((len(whitelist) < 1 or language in whitelist) and language not in blocked)

    def setAcceleration(self, video_codec):
        opts = []
        device = None
        # Look up which codecs and which decoders/encoders are available in this build of ffmpeg
        codecs = self.converter.ffmpeg.codecs

        # Lookup which hardware acceleration platforms are available in this build of ffmpeg
        hwaccels = self.converter.ffmpeg.hwaccels

        self.log.debug("Selected hwaccel options:")
        self.log.debug(self.settings.hwaccels)
        self.log.debug("Selected hwaccel decoder pairs:")
        self.log.debug(self.settings.hwaccel_decoders)
        self.log.debug("FFMPEG codecs:")
        self.log.debug(codecs)
        self.log.debug("FFMPEG decoders:")
        self.log.debug(hwaccels)

        # Find the first of the specified hardware acceleration platform that is available in this build of ffmpeg.  The order of specified hardware acceleration platforms determines priority.
        for hwaccel in self.settings.hwaccels:
            if hwaccel in hwaccels:
                device = self.settings.hwdevices.get(hwaccel)
                if device:
                    self.log.debug("Setting hwaccel device to %s." % device)
                    opts.extend(['-init_hw_device', '%s=mmt:%s' % (hwaccel, device)])
                    opts.extend(['-hwaccel_device', 'mmt'])

                self.log.info("%s hwaccel is supported by this ffmpeg build and will be used [hwaccels]." % hwaccel)
                opts.extend(['-hwaccel', hwaccel])
                if self.settings.hwoutputfmt.get(hwaccel):
                    opts.extend(['-hwaccel_output_format', self.settings.hwoutputfmt[hwaccel]])

                # If there's a decoder for this acceleration platform, also use it
                decoder = self.converter.ffmpeg.hwaccel_decoder(video_codec, hwaccel)
                self.log.debug("Decoder: %s." % decoder)
                if (decoder in codecs[video_codec]['decoders'] and decoder in self.settings.hwaccel_decoders):
                    self.log.info("%s decoder is also supported by this ffmpeg build and will also be used [hwaccel-decoders]." % decoder)
                    opts.extend(['-vcodec', decoder])
                break
        return opts, device

    def setDefaultAudioStream(self, audio_settings):
        if len(audio_settings) > 0:
            audio_streams = sorted(audio_settings, key=lambda x: x.get('channels', 1), reverse=True)
            audio_streams = sorted(audio_streams, key=lambda x: '+comment' in (x.get('disposition') or ''))
            preferred_language_audio_streams = [x for x in audio_streams if x.get('language') == 'eng'] if 'eng' else audio_streams
            default_stream = audio_streams[0]
            default_streams = [x for x in audio_streams if '+default' in (x.get('disposition') or '')]
            default_preferred_language_streams = [x for x in default_streams if x.get('language') == 'eng'] if 'eng' else default_streams
            default_streams_not_in_preferred_language = [x for x in default_streams if x not in default_preferred_language_streams]

            self.log.debug("%d total audio streams with %d set to default disposition. %d defaults in your preferred language (%s), %d in other languages." % (len(audio_streams), len(default_streams), len(default_preferred_language_streams), 'eng', len(default_streams_not_in_preferred_language)))
            if len(preferred_language_audio_streams) < 1:
                self.log.debug("No audio streams in your preferred language, using other languages to determine default stream.")

            if len(default_preferred_language_streams) < 1:
                try:
                    potential_streams = preferred_language_audio_streams if len(preferred_language_audio_streams) > 0 else default_streams
                    default_stream = potential_streams[0] if len(potential_streams) > 0 else audio_streams[0]
                except:
                    self.log.exception("Error setting default stream in preferred language.")
            elif len(default_preferred_language_streams) > 1:
                default_stream = default_preferred_language_streams[0]
                try:
                    for remove in default_preferred_language_streams[1:]:
                        if remove.get('disposition'):
                            remove['disposition'] = remove.get('disposition').replace('+default', '-default')
                    self.log.debug("%d streams in preferred language cleared of default disposition flag from preferred language." % (len(default_preferred_language_streams) - 1))
                except:
                    self.log.exception("Error in removing default disposition flag from extra audio streams, multiple streams may be set as default.")
            else:
                self.log.debug("Default audio stream already inherited from source material, will not override to audio-language-default.")
                default_stream = default_preferred_language_streams[0]

            default_streams_not_in_preferred_language = [x for x in default_streams_not_in_preferred_language if x != default_stream]
            if len(default_streams_not_in_preferred_language) > 0:
                self.log.debug("Cleaning up default disposition settings from not preferred languages. %d streams will have default flag removed." % (len(default_streams_not_in_preferred_language)))
                for remove in default_streams_not_in_preferred_language:
                    if remove.get('disposition'):
                        remove['disposition'] = remove.get('disposition').replace('+default', '-default')
            if default_stream.get('disposition'):
                default_stream['disposition'] = default_stream.get('disposition').replace('-default', '+default')
                if '+default' not in default_stream.get('disposition'):
                    default_stream['disposition'] += "+default"
            else:
                default_stream['disposition'] = "+default"

            self.log.info("Default audio stream set to %s %s %s channel stream [default-more-channels: True]." % (default_stream['language'], default_stream['codec'], default_stream['channels']))
        else:
            self.log.debug("Audio output is empty, unable to set default audio streams.")

    def setDefaultSubtitleStream(self, subtitle_settings):
        if len(subtitle_settings) > 0:
            if len([x for x in subtitle_settings if '+default' in (x.get('disposition') or '')]) < 1:
                default_stream = [x for x in subtitle_settings if x.get('language') == 'eng']
                if default_stream.get('disposition'):
                    default_stream['disposition'] = default_stream.get('disposition').replace('-default', '+default')
                    if '+default' not in default_stream.get('disposition'):
                        default_stream['disposition'] += '+default'
                else:
                    default_stream['disposition'] = '+default'

            else:
                self.log.debug("Default subtitle stream already inherited from source material, will not override to subtitle-language-default.")
        else:
            self.log.debug("Subtitle output is empty or no default subtitle language is set, will not pass over subtitle output to set a default stream.")

    def sortStreams(self, streams, languages):
        self.log.debug("Reordering streams to be in accordance with approved languages and channels [sort-streams, prefer-more-channels].")
        if len(streams) > 0:
            if isinstance(streams[0], dict):
                streams.sort(key=lambda x: x.get('channels', 999), reverse=True)
                if languages:
                    streams.sort(key=lambda x: languages.index(x.get('language')) if x.get('language') in languages else 999)
            else:
                streams.sort(key=lambda x: x.audio_channels, reverse=True)
                if languages:
                    streams.sort(key=lambda x: languages.index(x.metadata.get('language')) if x.metadata.get('language') in languages else 999)
                streams.sort(key=lambda x: x.disposition.get('comment'))

    def checkDisposition(self, allowed, source):
        for a in allowed:
            if not source.get(a):
                return False
        return True

    def dispoStringToDict(self, dispostr):
        dispo = {}
        if dispostr:
            d = re.findall('([+-][a-zA-Z]*)', dispostr)
            for x in d:
                dispo[x[1:]] = x.startswith('+')
        return dispo

    def scanForExternalSubs(self, inputFile, swl):
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        valid_external_subs = []
        for dirName, subdirList, fileList in os.walk(inputDir):
            for fname in fileList:
                subname, subextension = os.path.splitext(fname)
                # Watch for appropriate file extension
                if fname.startswith(filename):  # filename in fname:
                    valid_external_sub = self.isValidSubtitleSource(os.path.join(dirName, fname))
                    if valid_external_sub:
                        subname, langext = os.path.splitext(subname)
                        lang = 'und'
                        while langext:
                            lang = getAlpha3TCode(langext)
                            if lang != 'und':
                                break
                            subname, langext = os.path.splitext(subname)
                        if lang == 'und':
                            lang = 'eng'
                        valid_external_sub.subtitle[0].metadata['language'] = lang

                        if self.validLanguage(lang, swl):
                            self.log.debug("External %s subtitle file detected %s." % (lang, fname))
                            for dispo in BaseCodec.DISPOSITIONS:
                                valid_external_sub.subtitle[0].disposition[dispo] = ("." + dispo) in fname
                            valid_external_subs.append(valid_external_sub)
                        else:
                            self.log.debug("Ignoring %s external subtitle stream due to language %s." % (fname, lang))
            break
        self.log.info("Scanned for external subtitles and found %d results in your approved languages." % (len(valid_external_subs)))
        valid_external_subs.sort(key=lambda x: swl.index(x.subtitle[0].metadata['language']) if x.subtitle[0].metadata['language'] in swl else 999)

        return valid_external_subs

    def downloadSubtitles(self, inputFile, existing_subtitle_streams, swl, original=None):
        languages = set()
        for alpha3 in swl:
            try:
                languages.add(Language(alpha3))
            except:
                self.log.exception("Unable to add language for download with subliminal.")
        try:
            languages.add(Language('eng'))
        except:
            self.log.exception("Unable to add language for download with subliminal.")

        if len(languages) < 1:
            self.log.error("No valid subtitle download languages detected, subtitles will not be downloaded.")
            return []

        self.log.info("Attempting to download subtitles.")

        # Attempt to set the dogpile cache
        try:
            subliminal.region.configure('dogpile.cache.memory')
        except:
            pass

        try:
            video = subliminal.scan_video(os.path.abspath(inputFile))

            # If data about the original release is available, include that in the search to best chance at accurate subtitles
            if original:
                self.log.debug("Found original filename, adding data from %s." % original)
                og = subliminal.Video.fromname(original)
                self.log.debug("Source %s, release group %s, resolution %s." % (og.source, og.release_group, og.resolution))
                video.source = og.source
                video.release_group = og.release_group
                video.resolution = og.resolution

            subtitles = subliminal.download_best_subtitles([video], languages, hearing_impaired=False, provider_configs=self.settings.subproviders_auth)
            saves = subliminal.save_subtitles(video, subtitles[video])
            paths = [subliminal.subtitle.get_subtitle_path(video.name, x.language) for x in saves]

            for path in paths:
                self.log.info("Downloaded new subtitle %s." % path)
                self.setPermissions(path)

            return paths
        except:
            self.log.exception("Unable to download subtitles.")
        return []

    def setPermissions(self, path):
        try:
            os.chmod(path, self.settings.permissions.get('chmod', int('0664', 8)))
            if os.name != 'nt':
                os.chown(path, self.settings.permissions.get('uid', -1), self.settings.permissions.get('gid', -1))
        except:
            self.log.exception("Unable to set new file permissions.")

    def restoreFromOutput(self, inputFile, outputFile, resolution=None):
        if self.settings.outputDir and outputFile.startswith(self.settings.outputDir):
            inputDir, filename, inputExtension = self.parseFile(inputFile)
            newOutputFile, _ = self.getOutputFile(inputDir, filename, inputExtension, ignoreOutputDir=True, resolution=resolution)
            self.log.info("Output file is in outputDir %s, moving back to original directory %s." % (self.settings.outputDir, outputFile))
            shutil.move(outputFile, newOutputFile)
            return newOutputFile
        return outputFile

    def getSubExtensionFromCodec(self, codec):
        try:
            return subtitleCodecExtensions[codec]
        except:
            self.log.info("Wasn't able to determine subtitle file extension, defaulting to codec %s." % codec)
            return codec

    def getSubOutputFileFromOptions(self, inputFile, options, extension):
        language = options["language"]
        return self.getSubOutputFile(inputFile, language, options['disposition'], extension)

    def getSubOutputFile(self, inputFile, language, disposition, extension):
        disposition = self.dispoStringToDict(disposition)
        dispo = ""
        for k in disposition:
            if disposition[k] and k in ['forced']:
                dispo += "." + k
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        outputDir = self.settings.outputDir or inputDir
        outputFile = os.path.join(outputDir, filename + "." + language + dispo + "." + extension)

        i = 2
        while os.path.isfile(outputFile):
            self.log.debug("%s exists, appending %s to filename." % (outputFile, i))
            outputFile = os.path.join(outputDir, filename + "." + language + dispo + "." + str(i) + "." + extension)
            i += 1
        return outputFile

    def ripSubs(self, inputFile, ripSubOpts):
        rips = []
        for options in ripSubOpts:
            extension = self.getSubExtensionFromCodec(options['format'])
            outputFile = self.getSubOutputFileFromOptions(inputFile, options, extension)

            try:
                self.log.info("Ripping %s subtitle from source stream %s into external file." % (options["language"], options['index']))
                conv = self.converter.convert(outputFile, options, timeout=None)
                _, cmds = next(conv)
                self.log.debug("Subtitle extraction FFmpeg command:")
                self.log.debug(" ".join(str(item) for item in cmds))
                for timecode, debug in conv:
                    self.log.debug(debug)

                self.log.info("%s created." % outputFile)
                rips.append(outputFile)
            except (FFMpegConvertError, ConverterError):
                self.log.error("Unable to create external %s subtitle file for stream %s, may be an incompatible format." % (extension, options['index']))
                self.removeFile(outputFile)
                continue
            except:
                self.log.exception("Unable to create external subtitle file for stream %s." % (options['index']))
            self.setPermissions(outputFile)
        return rips

    def getOutputFile(self, inputDir, filename, inputExtension, tempExtension=None, ignoreOutputDir=False, number=0, resolution=None):
        if ignoreOutputDir:
            outputDir = inputDir
        else:
            outputDir = self.settings.outputDir or inputDir
        outputExtension = tempExtension or 'mp4'

        if resolution != None:
            splitName = filename.split('-')
            if splitName[len(splitName)-1] == 'copy':
                del splitName[-1]
            if splitName[len(splitName)-1] != str(resolution) + 'p':
                splitName[len(splitName)-2] = 'Transcoded'
            splitName[len(splitName)-1] = str(resolution) + 'p'
            filename = '-'.join(splitName)

        self.log.debug("Input directory: %s." % inputDir)
        self.log.debug("File name: %s." % filename)
        self.log.debug("Input extension: %s." % inputExtension)
        self.log.debug("Output directory: %s." % outputDir)
        self.log.debug("Output extension: %s." % outputDir)

        counter = (".%d" % number) if number > 0 else ""

        try:
            outputFile = os.path.join(outputDir.decode(sys.getfilesystemencoding()), filename.decode(sys.getfilesystemencoding()) + counter + "." + outputExtension).encode(sys.getfilesystemencoding())
        except:
            outputFile = os.path.join(outputDir, filename + counter + "." + outputExtension)

        self.log.debug("Output file: %s." % outputFile)
        return outputFile, outputDir

    def parseAndNormalize(self, inputstring, denominator, splitter="/"):
        n, d = [float(x) for x in inputstring.split(splitter)]
        if d == denominator:
            return n
        return int(round((n / d) * denominator))

    def hasValidFrameData(self, framedata):
        try:
            if 'side_data_list' in framedata:
                types = [x['side_data_type'] for x in framedata['side_data_list'] if 'side_data_type' in x]
                if 'Mastering display metadata' in types and 'Content light level metadata' in types:
                    return True
            return False
        except:
            return False

    def isImageBasedSubtitle(self, inputFile, map):
        ripSub = [{'map': map, 'codec': 'srt'}]
        options = {'source': [inputFile], 'format': 'srt', 'subtitle': ripSub}
        postopts = ['-t', '00:00:01']
        try:
            conv = self.converter.convert(None, options, timeout=30, postopts=postopts)
            _, cmds = next(conv)
            self.log.debug("isImageBasedSubtitle FFmpeg command:")
            self.log.debug(" ".join(str(item) for item in cmds))
            for timecode, debug in conv:
                self.log.debug(debug)
        except FFMpegConvertError:
            return True
        return False

    # Encode a new file based on selected options, built in naming conflict resolution
    def convert(self, options, preopts, postopts, reportProgress=False, progressOutput=None, resolution=None):
        self.log.info("Starting conversion.")
        inputFile = options['source'][0]
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        originalInputFile = inputFile
        outputFile, outputDir = self.getOutputFile(inputDir, filename, inputExtension, 'part', resolution=resolution)
        finalOutputFile, _ = self.getOutputFile(inputDir, filename, inputExtension, resolution=resolution)

        self.log.debug("Final output file: %s." % finalOutputFile)

        if len(options['audio']) == 0:
            self.log.error("Conversion has no audio streams, aborting")
            return None, inputFile

        # Check if input file and the final output file are the same and preferentially rename files (input first, then output if that fails)
        if os.path.abspath(inputFile) == os.path.abspath(finalOutputFile):
            self.log.debug("Inputfile and final outputFile are the same, trying to rename inputFile first.")
            try:
                og = inputFile + ".original"
                i = 2
                while os.path.isfile(og):
                    og = "%s.%d.original" % (inputFile, i)
                    i += 1
                os.rename(inputFile, og)
                inputFile = og
                options['source'][0] = og
                self.log.debug("Renamed original file to %s." % inputFile)

            except:
                i = 2
                while os.path.isfile(finalOutputFile):
                    outputFile, outputDir = self.getOutputFile(inputDir, filename, inputExtension, 'part', number=i, resolution=resolution)
                    finalOutputFile, _ = self.getOutputFile(inputDir, filename, inputExtension, number=i, resolution=resolution)
                    i += 1
                self.log.debug("Unable to rename inputFile. Alternatively renaming output file to %s." % outputFile)

        # Delete output file if it already exists and deleting enabled
        if os.path.exists(outputFile) and self.settings.delete:
            self.removeFile(outputFile)

        # Final sweep to make sure outputFile does not exist, renaming as the final solution
        i = 2
        while os.path.isfile(outputFile):
            outputFile, outputDir = self.getOutputFile(inputDir, filename, inputExtension, 'part', number=i, resolution=resolution)
            finalOutputFile, _ = self.getOutputFile(inputDir, filename, inputExtension, number=i, resolution=resolution)
            i += 1

        try:
            conv = self.converter.convert(outputFile, options, timeout=None, preopts=preopts, postopts=postopts, strip_metadata=True)
        except:
            self.log.exception("Error converting file.")
            return None, inputFile

        _, cmds = next(conv)
        self.log.info("FFmpeg command:")
        self.log.info("======================")
        self.log.info(" ".join("\"%s\"" % item if " " in item and "\"" not in item else item for item in cmds))
        self.log.info("======================")

        try:
            timecode = 0
            debug = ""
            for timecode, debug in conv:
                self.log.debug(debug)
                if reportProgress:
                    if progressOutput:
                        progressOutput(timecode, debug)
                    else:
                        self.displayProgressBar(timecode, debug)
            if reportProgress:
                if progressOutput:
                    progressOutput(100, debug)
                else:
                    self.displayProgressBar(100, newline=True)

            self.log.info("%s created." % outputFile)
            self.setPermissions(outputFile)

        except FFMpegConvertError as e:
            self.log.exception("Error converting file, FFMPEG error.")
            self.log.error(e.cmd)
            self.log.error(e.output)
            if os.path.isfile(outputFile):
                self.removeFile(outputFile)
                self.log.error("%s deleted." % outputFile)
            outputFile = None
            try:
                os.rename(inputFile, originalInputFile)
                return None, originalInputFile
            except:
                self.log.exception("Error restoring original inputFile after exception.")
                return None, inputFile
        except:
            self.log.exception("Unexpected exception during conversion.")
            try:
                os.rename(inputFile, originalInputFile)
                return None, originalInputFile
            except:
                self.log.exception("Error restoring original inputFile after FFMPEG error.")
                return None, inputFile

        # Check if the finalOutputFile differs from the outputFile. This can happen during above renaming or from temporary extension option
        if outputFile != finalOutputFile:
            self.log.debug("Outputfile and finalOutputFile are different attempting to rename to final extension [tempExtension].")
            try:
                os.rename(outputFile, finalOutputFile)
            except:
                self.log.exception("Unable to rename output file to its final destination file extension [tempExtension].")
                finalOutputFile = outputFile

        return finalOutputFile, inputFile

    def displayProgressBar(self, complete, debug="", width=20, newline=False):
        try:
            divider = 100 / width

            if complete > 100:
                complete = 100

            sys.stdout.write('\r')
            sys.stdout.write('[{0}] {1}% '.format('#' * int(round(complete / divider)) + ' ' * int(round(width - (complete / divider))), complete))
            if debug and self.settings.detailedprogress:
                if complete == 100:
                    sys.stdout.write("%s" % debug.strip())
                else:
                    sys.stdout.write(" %s" % debug.strip())
            if newline:
                sys.stdout.write('\n')
            sys.stdout.flush()
        except:
            print(complete)

    # Break apart a file path into the directory, filename, and extension
    def parseFile(self, path):
        path = os.path.abspath(path)
        inputDir, filename = os.path.split(path)
        filename, inputExtension = os.path.splitext(filename)
        inputExtension = inputExtension[1:]
        return inputDir, filename, inputExtension.lower()

    # Process a file with QTFastStart, removing the original file
    def QTFS(self, inputFile):
        inputDir, filename, inputExtension = self.parseFile(inputFile)
        temp_ext = '.QTFS'
        # Relocate MOOV atom to the very beginning. Can double the time it takes to convert a file but makes streaming faster
        if os.path.isfile(inputFile):
            from qtfaststart import processor, exceptions

            self.log.info("Relocating MOOV atom to start of file.")

            try:
                outputFile = inputFile.decode(sys.getfilesystemencoding()) + temp_ext
            except:
                outputFile = inputFile + temp_ext

            # Clear out the temp file if it exists
            if os.path.exists(outputFile):
                self.removeFile(outputFile, 0, 0)

            try:
                processor.process(inputFile, outputFile)
                self.setPermissions(outputFile)

                # Cleanup
                if self.removeFile(inputFile, replacement=outputFile):
                    return outputFile
                else:
                    self.log.error("Error cleaning up QTFS temp files.")
                    return False
            except exceptions.FastStartException:
                self.log.warning("QT FastStart did not run - perhaps moov atom was at the start already or file is in the wrong format.")
                return inputFile

    # Moves input file to directory specified in the move-to option
    def moveFile(self, inputFile, relativePath=None):
        files = [inputFile]

        if self.settings.moveTo:
            self.log.debug("Moveto option is enabled.")
            moveTo = os.path.join(self.settings.moveTo, relativePath) if relativePath else self.settings.moveTo
            if not os.path.exists(moveTo):
                os.makedirs(moveTo)
            try:
                shutil.move(inputFile, moveTo)
                self.log.info("%s moved to %s." % (inputFile, moveTo))
                files[0] = os.path.join(moveTo, os.path.basename(inputFile))
            except:
                self.log.exception("First attempt to move the file has failed.")
                try:
                    if os.path.exists(inputFile):
                        self.removeFile(inputFile, 0, 0)
                    shutil.move(inputFile.decode(sys.getfilesystemencoding()), moveTo)
                    self.log.info("%s moved to %s." % (inputFile, moveTo))
                    files[0] = os.path.join(moveTo, os.path.basename(inputFile))
                except:
                    self.log.exception("Unable to move %s to %s" % (inputFile, moveTo))
        for filename in files:
            self.log.debug("Final output file: %s." % filename)
        return files

    # Robust file removal function, with options to retry in the event the file is in use, and replace a deleted file
    def removeFile(self, filename, retries=2, delay=10, replacement=None):
        for i in range(retries + 1):
            try:
                # Make sure file isn't read-only
                os.chmod(filename, int("0777", 8))
            except:
                self.log.debug("Unable to set file permissions before deletion. This is not always required.")
            try:
                if os.path.exists(filename):
                    os.remove(filename)
                # Replaces the newly deleted file with another by renaming (replacing an original with a newly created file)
                if replacement:
                    os.rename(replacement, filename)
                    filename = replacement
                break
            except:
                self.log.exception("Unable to remove or replace file %s." % filename)
                if delay > 0:
                    self.log.debug("Delaying for %s seconds before retrying." % delay)
                    time.sleep(delay)
        return False if os.path.isfile(filename) else True

    def raw(self, text):
        escape_dict = {'\\': r'\\',
                       ':': "\\:"}
        output = ''
        for char in text:
            try:
                output += escape_dict[char]
            except KeyError:
                output += char
        return output
