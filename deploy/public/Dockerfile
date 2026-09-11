# Страница запуска для публичного сервера: всё в одном контейнере.
#
# Основа -- тот же образ OPM, что закреплён в кейсе: в нём уже есть flow,
# Python 3.12 и непривилегированный пользователь opm. Поэтому OPM считается
# прямо в контейнере, и доступ к Docker хоста странице не нужен: чужие сайты
# на сервере ей недоступны. Материалы организаторов не входят в образ, они
# подключаются томом только на чтение.
FROM openporousmedia/opmreleases@sha256:18c497f6a918c160cd290081d4163abf6e2c349797e18e3b5669c9095044adff

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-openpyxl python3-pytest \
    && rm -rf /var/lib/apt/lists/*

# Код считает, что OPM запускается через docker run; прокладка выполняет
# ту же команду напрямую, код решения не меняется.
COPY deploy/public/docker-shim /usr/local/bin/docker
RUN chmod 0755 /usr/local/bin/docker

WORKDIR /app
COPY --chown=opm:opm pyproject.toml README.md ./
COPY --chown=opm:opm cases ./cases
COPY --chown=opm:opm submission_evidence ./submission_evidence
COPY --chown=opm:opm src ./src
COPY --chown=opm:opm tests ./tests
RUN chown opm:opm /app

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MODEL_Y_ROOT=/materials/MODEL_Y \
    CHDD_ROOT=/materials/CHDD_PYTHON

USER opm
EXPOSE 8765
CMD ["python3", "-m", "aios.ui", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
