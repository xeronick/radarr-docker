import os
import sys
import locale

try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser as ConfigParser
try:
    from importlib import reload
except ImportError:
    pass
import logging
from resources.extensions import *


class MMTConfigParser(ConfigParser, object):
    def getList(self, section, option, vars=None, separator=",", default=[], lower=True, replace=[' ']):
        value = self.get(section, option, vars=vars)

        if not isinstance(value, str) and isinstance(value, list):
            return value

        if value == '':
            return list(default)

        value = value.split(separator)

        for r in replace:
            value = [x.replace(r, '') for x in value]
        if lower:
            value = [x.lower() for x in value]

        value = [x.strip() for x in value]
        return value

    def getDict(self, section, option, vars=None, listseparator=",", dictseparator=":", default={}, lower=True, replace=[' '], valueModifier=None):
        l = self.getList(section, option, vars, listseparator, [], lower, replace)
        output = dict(default)
        for listitem in l:
            split = listitem.split(dictseparator, 1)
            if len(split) > 1:
                if valueModifier:
                    try:
                        split[1] = valueModifier(split[1])
                    except:
                        self.log.exception("Invalid value for getDict")
                        continue
                output[split[0]] = split[1]
        return output

    def getPath(self, section, option, vars=None):
        path = self.get(section, option, vars=vars).strip()
        if path == '':
            return None
        return os.path.normpath(path)

    def getDirectory(self, section, option, vars=None):
        directory = self.getPath(section, option, vars)
        try:
            os.path.makedirs(directory)
        except:
            pass
        return directory

    def getDirectories(self, section, option, vars=None, separator=",", default=[]):
        directories = self.getList(section, option, vars=vars, separator=separator, default=default, lower=False)
        directories = [os.path.normpath(x) for x in directories]
        for d in directories:
            if not os.path.isdir(d):
                try:
                    os.path.makedirs(d)
                except:
                    pass
        return directories

    def getExtension(self, section, option, vars=None):
        extension = self.get(section, option, vars=vars).lower().replace(' ', '').replace('.', '')
        if extension == '':
            return None
        return extension

    def getExtensions(self, section, option, separator=",", vars=None):
        return self.getList(section, option, vars, separator, replace=[' ', '.'])

    def getInt(self, section, option, vars=None):
        if sys.version[0] == '2':
            return int(super(MMTConfigParser, self).get(section, option, vars=vars))
        return super(MMTConfigParser, self).getint(section, option, vars=vars)


class ReadSettings:
    defaults = {
        'Converter': {
            'ffmpeg': '/usr/local/bin/ffmpeg' if os.name != 'nt' else 'ffmpeg.exe',
            'ffprobe': '/usr/local/bin/ffprobe' if os.name != 'nt' else 'ffprobe.exe',
            'threads': 0,
            'hwaccels': '',
            'hwaccel-decoders': 'h264_cuvid, mjpeg_cuvid, mpeg1_cuvid, mpeg2_cuvid, mpeg4_cuvid, vc1_cuvid, hevc_qsv, h264_qsv, hevc_vaapi, h264_vaapi',
            'hwdevices': 'vaapi:/dev/dri/renderD128',
            'hwaccel-output-format': 'vaapi:vaapi',
            'output-directory': '',
            'ignored-extensions': 'nfo, ds_store',
            'move-to': '',
            'delete-original': True,
            'post-process': False,
            'wait-post-process': False,
            'detailed-progress': False,
            'attachment-codec': '',
        },
        'Permissions': {
            'chmod': '0644',
            'uid': -1,
            'gid': -1,
        },
        'Subtitle.Subliminal.Auth': {
            'opensubtitles': '',
            'tvsubtitles': '',
        },
        'Radarr': {
            'host': 'localhost',
            'port': 7878,
            'apikey': '',
            'ssl': False,
            'webroot': '',
            'force-rename': False,
            'rescan': True,
        },
        'Plex': {
            'host': 'localhost',
            'port': 32400,
            'refresh': False,
            'token': '',
        },
    }

    def __init__(self, configFile=None, logger=None):
        self.log = logger or logging.getLogger(__name__)

        self.log.info(sys.executable)
        if sys.version_info.major == 2:
            self.log.warning("Python 2 is no longer officially supported. Use with caution.")

        defaultConfigFile = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../config/autoProcess.ini"))
        envConfigFile = os.environ.get("MMT_CONFIG")

        if envConfigFile and os.path.exists(os.path.realpath(envConfigFile)):
            configFile = os.path.realpath(envConfigFile)
            self.log.debug("MMTCONFIG environment variable override found.")
        elif not configFile:
            configFile = defaultConfigFile
            self.log.debug("Loading default config file.")

        if os.path.isdir(configFile):
            new = os.path.realpath(os.path.join(os.path.join(configFile, "config"), "autoProcess.ini"))
            old = os.path.realpath(os.path.join(configFile, "autoProcess.ini"))
            if not os.path.exists(new) and os.path.exists(old):
                configFile = old
            else:
                configFile = new
            self.log.debug("ConfigFile specified is a directory, joining with autoProcess.ini.")

        self.log.info("Loading config file %s." % configFile)

        # Setup encoding to avoid UTF-8 errors
        if sys.version[0] == '2':
            SYS_ENCODING = None
            try:
                locale.setlocale(locale.LC_ALL, "")
                SYS_ENCODING = locale.getpreferredencoding()
            except (locale.Error, IOError):
                pass

            # For OSes that are poorly configured just force UTF-8
            if not SYS_ENCODING or SYS_ENCODING in ('ANSI_X3.4-1968', 'US-ASCII', 'ASCII'):
                SYS_ENCODING = 'UTF-8'

            if not hasattr(sys, "setdefaultencoding"):
                reload(sys)

            try:
                # pylint: disable=E1101
                # On non-unicode builds this will raise an AttributeError, if encoding type is not valid it throws a LookupError
                sys.setdefaultencoding(SYS_ENCODING)
            except:
                self.log.exception("Sorry, your environment is not setup correctly for utf-8 support. Please fix your setup and try again")
                sys.exit("Sorry, your environment is not setup correctly for utf-8 support. Please fix your setup and try again")

        write = False  # Will be changed to true if a value is missing from the config file and needs to be written

        config = MMTConfigParser()
        if os.path.isfile(configFile):
            config.read(configFile)
        else:
            self.log.error("Config file not found, creating %s." % configFile)
            # config.filename = filename
            write = True

        # Make sure all sections and all keys for each section are present
        for s in self.defaults:
            if not config.has_section(s):
                config.add_section(s)
                write = True
            for k in self.defaults[s]:
                if not config.has_option(s, k):
                    config.set(s, k, str(self.defaults[s][k]))
                    write = True

        # If any keys are missing from the config file, write them
        if write:
            self.writeConfig(config, configFile)

        self.readConfig(config)

    def readConfig(self, config):
        # Main converter settings
        section = "Converter"
        self.ffmpeg = config.getPath(section, 'ffmpeg', vars=os.environ)
        self.ffprobe = config.getPath(section, 'ffprobe', vars=os.environ)
        self.threads = config.getInt(section, 'threads')
        self.hwaccels = config.getList(section, 'hwaccels')
        self.hwaccel_decoders = config.getList(section, "hwaccel-decoders")
        self.hwdevices = config.getDict(section, "hwdevices", lower=False, replace=[])
        self.hwoutputfmt = config.getDict(section, "hwaccel-output-format")
        self.outputDir = config.getDirectory(section, "output-directory")
        self.ignoredExtensions = config.getExtensions(section, 'ignored-extensions')
        self.moveTo = config.getDirectory(section, "move-to")
        self.delete = config.getboolean(section, "delete-original")
        self.postprocess = config.getboolean(section, 'post-process')
        self.waitpostprocess = config.getboolean(section, 'wait-post-process')
        self.detailedprogress = config.getboolean(section, 'detailed-progress')
        self.attachmentcodec = config.getList(section, 'attachment-codec')
    
        # Permissions
        section = "Permissions"
        self.permissions = {}
        self.permissions['chmod'] = config.get(section, 'chmod')
        try:
            self.permissions['chmod'] = int(self.permissions['chmod'], 8)
        except:
            self.log.exception("Invalid permissions, defaulting to 644.")
            self.permissions['chmod'] = int("0644", 8)
        self.permissions['uid'] = config.getInt(section, 'uid', vars=os.environ)
        self.permissions['gid'] = config.getInt(section, 'gid', vars=os.environ)

        # Subliminal Auth Information
        section = "Subtitle.Subliminal.Auth"
        self.subproviders_auth = {}
        if config.has_section(section):
            for key in config[section]:
                try:
                    rawcredentials = config.get(section, key)
                    credentials = rawcredentials.split(":", 1)
                    if len(credentials) < 2:
                        if rawcredentials:
                            self.log.error("Unable to parse %s %s, skipping." % (section, key))
                        continue
                    credentials = [x.strip() for x in credentials]
                    self.subproviders_auth[key.strip()] = {'username': credentials[0], 'password': credentials[1]}
                except:
                    self.log.exception("Unable to parse %s %s, skipping." % (section, key))
                    continue

        # Radarr
        section = "Radarr"
        self.Radarr = {}
        self.Radarr['host'] = config.get(section, "host")
        self.Radarr['port'] = config.getInt(section, "port")
        self.Radarr['apikey'] = config.get(section, "apikey")
        self.Radarr['ssl'] = config.getboolean(section, "ssl")
        self.Radarr['webroot'] = config.get(section, "webroot")
        if not self.Radarr['webroot'].startswith("/"):
            self.Radarr['webroot'] = "/" + self.Radarr['webroot']
        if self.Radarr['webroot'].endswith("/"):
            self.Radarr['webroot'] = self.Radarr['webroot'][:-1]
        self.Radarr['rename'] = config.getboolean(section, "force-rename")
        self.Radarr['rescan'] = config.getboolean(section, "rescan")

        # Plex
        section = "Plex"
        self.Plex = {}
        self.Plex['host'] = config.get(section, "host")
        self.Plex['port'] = config.getInt(section, "port")
        self.Plex['refresh'] = config.getboolean(section, "refresh")
        self.Plex['token'] = config.get(section, "token")

    def writeConfig(self, config, cfgfile):
        if not os.path.isdir(os.path.dirname(cfgfile)):
            os.makedirs(os.path.dirname(cfgfile))
        try:
            fp = open(cfgfile, "w")
            config.write(fp)
            fp.close()
        except IOError:
            self.log.exception("Error writing to autoProcess.ini.")
        except PermissionError:
            self.log.exception("Error writing to autoProcess.ini due to permissions.")
