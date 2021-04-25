#!/usr/bin/env python3
import os
import sys
import requests
import time
import shutil
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.metadata import MediaType
from resources.mediaprocessor import MediaProcessor


# Radarr API functions
def rescanAndWait(baseUrl, headers, movieId, log, retries=6, delay=10):
    url = baseUrl + "/api/v3/command"
    log.debug("Queueing rescan command to Radarr via %s." % url)

    # First trigger rescan
    payload = {'name': 'RescanMovie', 'movieId': movieId}
    log.debug(str(payload))

    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.debug(str(rstate))
    log.debug("Radarr response from RescanMovie command: ID %d %s." % (rstate['id'], rstate['status']))

    # Then wait for it to finish
    url = baseUrl + "/api/v3/command/" + str(rstate['id'])
    log.debug("Requesting command status from Sonarr via %s." % url)
    r = requests.get(url, headers=headers)
    command = r.json()

    attempts = 0
    while command['status'].lower() not in ['complete', 'completed'] and attempts < retries:
        log.debug("Status: %s." % (command['status']))
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    log.debug(str(command))
    log.debug("Final status: %s." % (command['status']))
    return command['status'].lower() in ['complete', 'completed']


def renameRequest(baseUrl, headers, movieId, log):
    url = baseUrl + "/api/command"
    log.debug("Queueing rename command to Radarr via %s." % url)

    payload = {'name': 'RenameMovie', 'movieIds': [movieId]}
    log.debug(str(payload))
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    return rstate


def downloadedMoviesScanInProgress(baseUrl, headers, movieFileSourceFolder, log):
    url = baseUrl + "/api/v3/command"
    log.debug("Requesting commands in process from Radarr via %s." % url)
    r = requests.get(url, headers=headers)
    commands = r.json()
    log.debug(commands)
    log.debug(movieFileSourceFolder)
    for c in commands:
        if c.get('name') == "DownloadedMoviesScan":
            try:
                if c['body']['path'] == movieFileSourceFolder and c['status'] == 'started':
                    log.debug("Found a matching path scan in progress %s." % (movieFileSourceFolder))
                    return True
            except:
                pass
    log.debug("No commands in progress for %s." % (movieFileSourceFolder))
    return False


def getMovie(baseUrl, headers, movieId, log):
    url = baseUrl + "/api/v3/movie/" + str(movieId)
    log.debug("Requesting movie from Radarr via %s." % url)
    r = requests.get(url, headers=headers)
    payload = r.json()
    return payload


def updateMovie(baseUrl, headers, new, movieId, log):
    url = baseUrl + "/api/v3/movie/" + str(movieId)
    log.debug("Requesting movie update to Radarr via %s." % url)
    r = requests.put(url, json=new, headers=headers)
    payload = r.json()
    return payload


def getMovieFile(baseUrl, headers, movieFileId, log):
    url = baseUrl + "/api/v3/moviefile/" + str(movieFileId)
    log.debug("Requesting moviefile from Radarr for moviefile via %s." % url)
    r = requests.get(url, headers=headers)
    payload = r.json()
    return payload


def updateMovieFile(baseUrl, headers, new, movieFileId, log):
    url = baseUrl + "/api/v3/moviefile/" + str(movieFileId)
    log.debug("Requesting moviefile update to Radarr via %s." % url)
    r = requests.put(url, json=new, headers=headers)
    payload = r.json()
    return payload


# Rename functions
def restoreSceneName(inputFile, sceneName):
    if sceneName:
        directory = os.path.dirname(inputFile)
        extension = os.path.splitext(inputFile)[1]
        os.rename(inputFile, os.path.join(directory, "%s%s" % (sceneName, extension)))


def renameFile(inputFile, log):
    filename, fileExt = os.path.splitext(inputFile)
    outputFile = "%s.rnm%s" % (filename, fileExt)
    i = 2
    while os.path.isfile(outputFile):
        outputFile = "%s.rnm%d%s" % (filename, i, fileExt)
        i += 1
    os.rename(inputFile, outputFile)
    log.debug("Renaming file %s to %s." % (inputFile, outputFile))
    return outputFile


def backupSubs(inputpath, mp, log, extension=".backup"):
    dirname, filename = os.path.split(inputpath)
    files = []
    output = {}
    for r, _, f in os.walk(dirname):
        for file in f:
            files.append(os.path.join(r, file))
    for filePath in files:
        if filePath.startswith(os.path.splitext(filename)[0]):
            info = mp.isValidSubtitleSource(filePath)
            if info:
                newPath = filePath + extension
                shutil.copy2(filePath, newPath)
                output[newPath] = filePath
                log.info("Copying %s to %s." % (filePath, newPath))
    return output


def restoreSubs(subs, log):
    for k in subs:
        try:
            os.rename(k, subs[k])
            log.info("Restoring %s to %s." % (k, subs[k]))
        except:
            os.remove(k)
            log.exception("Unable to restore %s, deleting." % (k))


log = getLogger("RadarrPostProcess")

log.info("Radarr extra script post processing started.")

if os.environ.get('radarr_eventtype') == "Test":
    sys.exit(0)

settings = ReadSettings()

log.debug(os.environ)

try:
    inputFile = os.environ.get('radarr_moviefile_path')
    original = os.environ.get('radarr_moviefile_sceneName')
    imdbId = os.environ.get('radarr_movie_imdbid')
    tmdbId = os.environ.get('radarr_movie_tmdbid')
    movieId = int(os.environ.get('radarr_movie_id'))
    movieFileId = int(os.environ.get('radarr_moviefile_id'))
    sceneName = os.environ.get('radarr_moviefile_sceneName')
    releaseGroup = os.environ.get('radarr_moviefile_releasegroup')
    movieFileSourceFolder = os.environ.get('radarr_moviefile_sourcefolder')
except:
    log.exception("Error reading environment variables")
    sys.exit(1)

mp = MediaProcessor(settings)

log.debug("Input file: %s." % inputFile)
log.debug("Original name: %s." % original)
log.debug("IMDB ID: %s." % imdbId)
log.debug("TMDB ID: %s." % tmdbId)
log.debug("Radarr Movie ID: %d." % movieId)

try:
    if settings.Radarr.get('rename'):
        # Prevent asynchronous errors from file name changing
        mp.settings.waitpostprocess = True
        try:
            inputFile = renameFile(inputFile, log)
        except:
            log.exception("Error renaming inputFile.")

    success = mp.fullprocess(inputFile, MediaType.Movie, original=original, tmdbId=tmdbId, imdbId=imdbId)

    if success and not settings.Radarr['rescan']:
        log.info("File processed successfully and rescan API update disabled.")
    elif success:
        # Update Radarr to continue monitored status
        try:
            host = settings.Radarr['host']
            port = settings.Radarr['port']
            webroot = settings.Radarr['webroot']
            apiKey = settings.Radarr['apikey']
            ssl = settings.Radarr['ssl']
            protocol = "https://" if ssl else "http://"
            baseUrl = protocol + host + ":" + str(port) + webroot

            log.debug("Radarr baseUrl: %s." % baseUrl)
            log.debug("Radarr apiKey: %s." % apiKey)

            if apiKey != '':
                headers = {'X-Api-Key': apiKey}

                subs = backupSubs(success[0], mp, log)

                if downloadedMoviesScanInProgress(baseUrl, headers, movieFileSourceFolder, log):
                    log.info("DownloadedMoviesScan command is in process for this movie, cannot wait for rescan but will queue.")
                    rescanAndWait(baseUrl, headers, movieId, log, retries=0)
                    renameRequest(baseUrl, headers, movieId, log)
                elif rescanAndWait(baseUrl, headers, movieId, log):
                    log.info("Rescan command completed successfully.")

                    movieInfo = getMovie(baseUrl, headers, movieId, log)
                    if not movieInfo:
                        log.error("No valid movie information found, aborting.")
                        sys.exit(1)

                    if not movieInfo.get('hasFile'):
                        log.warning("Rescanned movie does not have a file, attempting second rescan.")
                        if rescanAndWait(baseUrl, headers, movieId, log):
                            movieInfo = getMovie(baseUrl, headers, movieId, log)
                            if not movieInfo.get('hasFile'):
                                log.warning("Rescanned movie still does not have a file, will not set to monitored to prevent endless loop.")
                                sys.exit(1)
                            else:
                                log.info("File found after second rescan.")
                        else:
                            log.error("Rescan command timed out.")
                            restoreSubs(subs, log)
                            sys.exit(1)

                    if len(subs) > 0:
                        log.debug("Restoring %d subs and triggering a final rescan." % (len(subs)))
                        restoreSubs(subs, log)
                        rescanAndWait(baseUrl, headers, movieId, log)

                    # Then set that movie to monitored
                    try:
                        movieInfo['monitored'] = True
                        movieInfo = updateMovie(baseUrl, headers, movieInfo, movieId, log)
                        log.debug(str(movieInfo))
                        log.info("Radarr monitoring information updated for movie %s." % movieInfo['title'])
                    except:
                        log.exception("Failed to restore monitored status to movie.")

                    if sceneName or releaseGroup:
                        log.debug("Trying to restore scene information.")
                        try:
                            mf = getMovieFile(baseUrl, headers, movieInfo['movieFile']['id'], log)
                            mf['sceneName'] = sceneName
                            mf['releaseGroup'] = releaseGroup
                            mf = updateMovieFile(baseUrl, headers, mf, movieInfo['movieFile']['id'], log)
                            log.debug("Restored releaseGroup to %s." % mf.get('releaseGroup'))
                        except:
                            log.exception("Unable to restore scene information.")

                    # Now a final rename step to ensure all release / codec information is accurate
                    try:
                        rename = renameRequest(baseUrl, headers, movieId, log)
                        log.info("Radarr response Rename command: ID %d %s." % (rename['id'], rename['status']))
                    except:
                        log.exception("Failed to trigger Radarr rename.")
                else:
                    log.error("Rescan command timed out.")
                    sys.exit(1)
            else:
                log.error("Your Radarr API Key is blank. Update autoProcess.ini to enable status updates.")
        except:
            log.exception("Radarr monitor status update failed.")
    else:
        log.info("Processing returned False.")
        sys.exit(1)
except:
    log.exception("Error processing file.")
    sys.exit(1)