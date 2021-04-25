import os
import logging
import json
from subprocess import Popen, PIPE
from resources.extensions import badPostFiles, badPostExtensions
from resources.metadata import MediaType


class PostProcessor:
    def __init__(self, files, logger=None, wait=False):
        self.log = logger or logging.getLogger(__name__)

        self.log.debug("Output: %s." % files)

        self.set_script_environment(files)
        self.scripts = self.gather_scripts()
        self.wait = wait

    def set_script_environment(self, files):
        self.log.debug("Setting script environment.")
        self.post_process_environment = os.environ.copy()
        self.post_process_environment['MMT_FILES'] = json.dumps(files)

    def gather_scripts(self):
        self.log.debug("Gathering scripts.")
        current_directory = os.path.dirname(os.path.realpath(__file__))
        post_process_directory = os.path.join(current_directory, '../post_process')
        scripts = []
        for script in sorted(os.listdir(post_process_directory)):
            if os.path.splitext(script)[1] in badPostExtensions or os.path.isdir(script) or script in badPostFiles:
                self.log.debug("Skipping %s." % script)
                continue
            else:
                self.log.debug("Script added: %s." % script)
                scripts.append(os.path.join(post_process_directory, script))
        return scripts

    def setEnv(self, mediatype, tmdbId, season=None, episode=None):
        if mediatype == MediaType.TV:
            self.setTV(tmdbId, season, episode)
        elif mediatype == MediaType.Movie:
            self.setMovie(tmdbId)

    def setTV(self, tmdbId, season, episode):
        self.log.debug("Setting TV metadata.")
        self.post_process_environment['MMT_TMDBID'] = str(tmdbId)
        self.post_process_environment['MMT_SEASON'] = str(season)
        self.post_process_environment['MMT_EPISODE'] = str(episode)

    def setMovie(self, tmdbId):
        self.log.debug("Setting movie metadata.")
        self.post_process_environment['MMT_TMDBID'] = str(tmdbId)

    def run_scripts(self):
        self.log.debug("Running scripts.")
        for script in self.scripts:
            try:
                command = self.run_script_command(script)
                self.log.info("Running script '%s'." % (script))
                stdout, stderr = command.communicate()
                self.log.debug("Stdout: %s." % stdout)
                self.log.debug("Stderr: %s." % stderr)
                if self.wait:
                    status = command.wait()
            except:
                self.log.exception("Failed to execute script %s." % script)

    def run_script_command(self, script):
        return Popen([str(script)], shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=self.post_process_environment,
                     close_fds=(os.name != 'nt'))
