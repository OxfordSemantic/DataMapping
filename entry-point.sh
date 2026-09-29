#!/bin/bash

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <data_file> <mapping_file> <output_ttl>"
    exit 1
else
    if [ ! -f "/data/$1" ]; then
        echo "Data file not found: /data/$1" >&2
        echo "Ensure you have mounted your data directory to container path /data?" >&2
        exit 1
    fi
    if [ ! -f "/data/$2" ]; then
        echo "Mapping file not found: /data/$2" >&2
        echo "Ensure you have mounted your data directory to container path /data?" >&2
        exit 1
    fi
fi

python -m data_mapping "/data/$1" "/data/$2" "/data/$3"
