FROM whyour/qinglong:2.20.2

RUN apk add --no-cache util-linux \
    && pip3 install --no-cache-dir --ignore-installed \
        --target /usr/local/lib/python3.11/site-packages \
        requests beautifulsoup4
