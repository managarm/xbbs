FROM managarm_buildenv

USER root
RUN mkdir /builder
RUN chmod 1777 /builder
# until xbstrap updates and xbbs is released
RUN pip3 install -U https://github.com/managarm/xbstrap/archive/master.zip
RUN pip3 install -U https://github.com/managarm/xbbs/archive/master.zip
RUN apt-get update && apt-get install -y libguestfs-tools
RUN mkdir /etc/xbbs
RUN mkdir -p /var/local
RUN echo 'submit_endpoint = "tcp://127.0.0.1:16001"' >/etc/xbbs/worker.toml

USER managarm_buildenv
WORKDIR /builder

CMD ["xbbs-worker"]
