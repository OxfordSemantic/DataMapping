FROM ubuntu:24.04
COPY install-python-dependencies.sh .
RUN . ./install-python-dependencies.sh
RUN rm ./install-python-dependencies.sh

WORKDIR /app
COPY data_mapping/ ./data_mapping/
COPY schema/ ./schema/
COPY Pipfile ./Pipfile
COPY Pipfile.lock ./Pipfile.lock
RUN mkdir .venv
RUN pipenv install
ENV PATH="/app/.venv/bin:$PATH"

COPY ./entry-point.sh ./entry-point.sh
RUN chmod +x /app/entry-point.sh
ENTRYPOINT [ "/app/entry-point.sh" ]
