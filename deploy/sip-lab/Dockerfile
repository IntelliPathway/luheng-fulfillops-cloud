FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends asterisk ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/asterisk && chown asterisk:asterisk /run/asterisk
COPY start.sh /usr/local/bin/start-sip-lab
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-sip-lab"]
