import os
import sys

from data_mapping import convert_csv_to_rdf, convert_parquet_to_rdf

def main():
    if len(sys.argv) != 4:
        print("Usage: python main.py <data_file> <mapping_file> <output_ttl>")
        sys.exit(1)
    data_file_extension = os.path.splitext(sys.argv[1])[-1].lower()
    if data_file_extension == ".csv":
        convert_csv_to_rdf(sys.argv[1], sys.argv[2], sys.argv[3])
    elif data_file_extension == ".parquet":
        convert_parquet_to_rdf(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print(f"Invalid extension for data file ('{data_file_extension}'). Use '.csv' or '.parquet'.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        sys.exit(1)
