#!/bin/bash

DEBIAN_FRONTEND=noninteractive

apt update

apt upgrade -y

apt -y install --no-install-recommends python3 pipenv

apt clean

rm -rf /var/lib/apt/lists/*
