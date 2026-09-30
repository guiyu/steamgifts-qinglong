FROM whyour/qinglong:2.20.2

RUN apk add --no-cache util-linux \
    && pip3 install --no-cache-dir requests beautifulsoup4
