#!/bin/sh
set -eu
# Set mode while root still owns the copies, then transfer ownership.
# install -o/-g changes ownership before chmod and requires extra FOWNER.
install -m 600 /run/sip-lab/pjsip.conf /etc/asterisk/pjsip.conf
install -m 600 /run/sip-lab/ari.conf /etc/asterisk/ari.conf
chown asterisk:asterisk /etc/asterisk/pjsip.conf /etc/asterisk/ari.conf
exec asterisk -f -vv -U asterisk -G asterisk -C /etc/asterisk/asterisk.conf
