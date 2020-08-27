FROM managarm_buildenv
# TODO(arsen): remove references to xbci: it's now xbbs

USER root
RUN mkdir /builder
RUN chmod 1777 /builder
# until xbstrap updates and xbbs is released
RUN pip3 install -U https://github.com/managarm/xbstrap/archive/master.zip
RUN pip3 install https://owo.codes/ArsenArsen/xbci/-/archive/master/xbci-master.tar.gz
RUN mkdir /etc/xbci
RUN echo 'submit_endpoint = "tcp://127.0.0.1:16001"' >/etc/xbci/worker.toml

USER managarm_buildenv
WORKDIR /builder

CMD ["xbci-worker"]
