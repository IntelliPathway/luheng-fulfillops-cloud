#!/bin/sh
set -eu
# Root only copies private bind-mounted configuration; Asterisk drops to its user.
install -o asterisk -g asterisk -m 600 /run/sip-lab/pjsip.conf /etc/asterisk/pjsip.conf
install -o asterisk -g asterisk -m 600 /run/sip-lab/ari.conf /etc/asterisk/ari.conf
exec asterisk -f -vv -U asterisk -G asterisk -C /etc/asterisk/asterisk.conf
