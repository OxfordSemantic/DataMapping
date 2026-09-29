# DataMapping

This is a project that maps tabular data to RDF. It contains a jsonschema document specifying the format of a mapping file (`schema/mapping-schema.json`), and a reference implementation for applying mapping documents conforming to said jsonschema to tabular input data to transform it to RDF data. The reference implementation is written in Python.

## Pre-requisites

This project requires Python 3.12 and [pipenv](https://pipenv.pypa.io/en/latest/index.html). The recommended way to install pipenv is with [pipx](https://pipx.pypa.io/stable/installation/).

Running the tests in the repository requires RDFox on the path.

## Running the data mapping tool

Clone this repository and then, from the root directory, run `pipenv install` to install the dependencies. You only have to do this once after cloning the repository unless any new dependencies are added

With the dependencies installed, launch a shell with all the correct Python libraries available by running `pipenv shell`. From this new shell, you can invoke the mapping tool using the following syntax:

```
python -m data_mapping <data_file> <mapping_file> <output_ttl>
```

## Example data sets

The `examples` directory contains several example inputs, each contained within a folder with its name.

## Docker

### Building the Docker image for local testing

Build the Docker image with `docker build . -t oxfordsemantic/data-mapping:local`

### Running the Docker image

After building the Docker image, you can run it by mapping the directory containing your input files to `/data` and specifying the three file names. For example, to run the `titanic` example from the root of this repository, you would run:

```
docker run --rm -v ./examples/titanic:/data oxfordsemantic/data-mapping:local data.parquet mapping.json output.ttl
```

This will write the transformed data to `examples/titanic/output.ttl`.

## Versioning and publishing

This repository is associated with the following version numbers:

- a schema version, which should only change when the schema at path `schema/mapping-schema.json` changes, and
- a version for the reference implementation version, which should change whenever the code in the `data_mapping` directory changes.

The schema version number is recorded in the schema document itself whereas the reference implementation version, which is used as the overall version number for the repository, is maintained in the file `version.txt`.

### How and when to change the schema version

The schema version should be updated whenever the schema definition at path `schema/mapping-schema.json` changes, using [Semantic Versioning](https://semver.org/).

Please ensure that all references to the schema version within the repository remain in sync with the version in the above document taking care not to confuse other version numbers (e.g. the reference implementation version or the mapping version numbers in examples).

### How and when to change the reference implementation version and how to publish new releases

The reference implementation is also versioned semantically. When considering whether a new version is backwards compatible with the previous version, the command line interface, supported schema version and the output that is generated should all be considered. If any of these aspects change in a way that would prevent dependent scripts from upgrading without any other modification, the new version will be considered a major version.

Note: at the time of writing, the code in this repository has not yet reached a level of maturity sufficient for v1.0.0 - please see clause 5 of the Semantic Versioning [spec](https://semver.org/).

## Assorted rules for the schema

- IRI references, including those in lexical form templates, may use either full or prefixed IRI syntax.
    - IRI references using full IRI syntax must begin with a less-than symbol ('<') and end with a greater than symbol ('>').
    - IRI references using prefixed IRI syntax must begin with a prefix that has been declared in the mapping and must contain only characters valid for local names in Turtle. Note that there is no equivalent requirement for full IRIs or literals. Illegal characters will be escaped correctly.
- Curly braces cannot be used in the static part of any template.
- As per [R2RML's rule on templates](https://www.w3.org/TR/r2rml/#from-template), if a lexical form template contains multiple pairs of unescaped curly braces, then any pair SHOULD be separated from the next one by a safe separator. This is any character or string that does not occur anywhere in any of the data values of either referenced column; or in the IRI-safe versions of the data values, if the term type is IRI.
    - This is necessary to guarantee unique value mappings for unique sets of values to be substituted.
