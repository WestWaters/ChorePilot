ARG BUILD_FROM
FROM ${BUILD_FROM}

# The package source is copied in by ../sync.sh (an add-on's build context is its own folder).
# Once ChorePilot is on PyPI this can be `pip install chorepilot==<version>` instead.
COPY src /src
RUN pip3 install --no-cache-dir /src && rm -rf /src
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
