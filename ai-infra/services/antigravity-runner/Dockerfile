FROM public.ecr.aws/docker/library/python:3.12-slim

# Official Linux ARM64 release; pinned rather than a mutable installer.
RUN python -c "import urllib.request,hashlib,tarfile,io; data=urllib.request.urlopen('https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.5-4931130160447488/linux-arm/cli_linux_arm64.tar.gz').read(); assert hashlib.sha512(data).hexdigest() == 'e11c990024b3f253fdd6ffd552e117478cb320bbf5bf2ff67a218fada718c8eeebad7cd916b691e39a3b206a09bc754d177319b002c1aaa546ab4e5ee4633bf0'; archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:gz'); member=archive.extractfile('antigravity'); open('/usr/local/bin/agy','wb').write(member.read())" \
    && chmod 755 /usr/local/bin/agy \
    && useradd --create-home --uid 10001 agent
USER agent
WORKDIR /home/agent
ENTRYPOINT ["agy"]
