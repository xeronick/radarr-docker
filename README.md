# xeronick/radarr
A docker container based on linuxserver/radarr with a mp4 multi-bitrate transcoder baked in

## Usage
````
docker create \
    --name radarr \
    --restart unless-stopped \
    -p 7878:7878 \
    -e PUID=1001 -e PGID=1001 \
    -e TZ="Europe/London"  \
    -v <path to config data>:/config \
    -v <path to movies data>:/movies \
    -v <path to downloads data>:/downloads \
    xeronick/radarr
    
mkdir <path to config data>/transcoder && \
wget https://raw.githubusercontent.com/xeronick/radarr/main/transcoder/setup/autoProcess.ini.sample -O <path to config data>/transcoder/autoProcess.ini
````

## Parameters
See [https://hub.docker.com/r/linuxserver/radarr/](https://hub.docker.com/r/linuxserver/radarr/) for details.

## mkdir
Makes a symlink from the transcoder directory to a config directory that is a volume for the container. If the host has a config file (autoProcess.ini) in the mounted volume, then the multi-bitrate mp4 transcoder will be able to use that. This is useful as it allows the modification of the config from the host OS.
