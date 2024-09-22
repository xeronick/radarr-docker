FROM ghcr.io/linuxserver/radarr

ENV MMT_UPDATE false
ENV MMT_FFMPEG_URL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
ENV MMT_OPENSSL_VERSION 1.1.1k

RUN \
  apk update && \
  apk add --no-cache \
  ffmpeg \
  git \
  python3 \
  py3-pip \
  py3-virtualenv \
  php-cli \
  nano \
  wget

# Setup transcoder
RUN mkdir /transcoder
COPY root/ /
RUN \
  python3 -m virtualenv /transcoder/venv && \
  /transcoder/venv/bin/pip install -r /transcoder/setup/requirements.txt

# Download and install FFMPEG + FFPROBE
RUN \
  wget ${MMT_FFMPEG_URL} -O /tmp/ffmpeg.tar.xz && \
  tar -xJf /tmp/ffmpeg.tar.xz -C /usr/local/bin --strip-components 1 && \
  chgrp users /usr/local/bin/ffmpeg && \
  chgrp users /usr/local/bin/ffprobe && \
  chmod g+x /usr/local/bin/ffmpeg && \
  chmod g+x /usr/local/bin/ffprobe

# Download and install OpenSSL
RUN \
  wget https://www.openssl.org/source/openssl-${MMT_OPENSSL_VERSION}.tar.gz -O /tmp/openssl-${MMT_OPENSSL_VERSION}.tar.gz && \
  tar -zxf /tmp/openssl-${MMT_OPENSSL_VERSION}.tar.gz && cd openssl-${MMT_OPENSSL_VERSION} && \
  ./config && \
  make && \
  make test && \
  rm -rf /usr/bin/openssl && \
  make install && \
  ln -s /usr/local/bin/openssl /usr/bin/openssl && \
  ldconfig

# Clean-up
RUN \
  ln -s /downloads /data && \
  ln -s /config/transcoder/autoProcess.ini /transcoder/config/autoProcess.ini && \
  apk del --purge && \
  rm -rf \
    /root/.cache \
    /tmp/* \
    /var/tmp/*
