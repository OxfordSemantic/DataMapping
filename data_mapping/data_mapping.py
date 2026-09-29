import io
import json
import os
import re
from typing import TextIO
import urllib

from jsonschema import validate
from jsonschema.exceptions import ValidationError
import pandas as pd
import pyarrow.parquet as pq


BATCH_SIZE = 1000


PREFIX_NAME_REGEX = re.compile(r"^[A-Za-z](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
UNESCAPABLE_PREFIXED_IRI_CHARACTERS = {'<', '>', '"', '{', '}', '|', '^', '`', '\\'}


def _is_full_iri(value: str) -> bool:
    return value.startswith("<") and value.endswith(">")


def _is_prefixed_iri(value: str) -> bool:
    return ":" in value and not _is_full_iri(value)


def _validate_prefixes(prefixes: dict[str, str], mapping_file: str | os.PathLike) -> None:
    for prefix_name, prefix_iri in prefixes.items():
        if prefix_name != "" and not PREFIX_NAME_REGEX.match(prefix_name):
            raise Exception(
                f"The mapping file at '{mapping_file}' contains an invalid prefix name '{prefix_name}'."
            )
        if not _is_full_iri(prefix_iri):
            raise Exception(
                f"The mapping file at '{mapping_file}' contains an invalid prefix declaration for '{prefix_name}': "
                f"'{prefix_iri}'. Prefix IRIs must start with '<' and end with '>'."
            )


def _validate_prefixed_iri(value: str, context: str, mapping_file: str | os.PathLike) -> None:
    _, local_part_template = value.split(":", 1)
    static_parts = re.split(r"\{[^{}]+\}", local_part_template)

    for static_part in static_parts:
        for character in static_part:
            if ord(character) <= 0x20 or character in UNESCAPABLE_PREFIXED_IRI_CHARACTERS:
                raise Exception(
                    f"The mapping file at '{mapping_file}' contains an invalid prefixed IRI '{value}' in {context}. "
                    f"The character '{character}' cannot be used there because static parts of prefixed IRIs are not escaped."
                )

    if local_part_template.endswith("."):
        raise Exception(
            f"The mapping file at '{mapping_file}' contains an invalid prefixed IRI '{value}' in {context}. "
            "Prefixed IRIs must not end with a dot."
        )


def _validate_mapping_references(mapping: dict, mapping_file: str | os.PathLike) -> None:
    prefixes = mapping.get("prefixes", {})
    for triple_map_index, triple_map in enumerate(mapping["tripleMaps"]):
        subject_map = triple_map["subjectMap"]
        values_to_check: list[tuple[str, str, bool]] = [
            (subject_map["value"], f"tripleMaps[{triple_map_index}].subjectMap.value", True),
        ]
        for class_index, subject_class in enumerate(subject_map.get("classes", [])):
            values_to_check.append((subject_class, f"tripleMaps[{triple_map_index}].subjectMap.classes[{class_index}]", False))

        for predicate_object_map_index, predicate_object_map in enumerate(triple_map["predicateObjectMaps"]):
            object_map = predicate_object_map["objectMap"]
            values_to_check.append((predicate_object_map["predicate"], f"tripleMaps[{triple_map_index}].predicateObjectMaps[{predicate_object_map_index}].predicate", False))
            if object_map["type"] != "IRI":
                values_to_check.append((object_map["type"], f"tripleMaps[{triple_map_index}].predicateObjectMaps[{predicate_object_map_index}].objectMap.type", False))
            else:
                values_to_check.append((object_map["value"], f"tripleMaps[{triple_map_index}].predicateObjectMaps[{predicate_object_map_index}].objectMap.value", True))
                for class_index, object_class in enumerate(object_map.get("classes", [])):
                    values_to_check.append((object_class, f"tripleMaps[{triple_map_index}].predicateObjectMaps[{predicate_object_map_index}].objectMap.classes[{class_index}]", False))

            if object_map["type"] in ["rdf:langString", "<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>"] and "lang" not in object_map:
                raise Exception(
                    f"The mapping file at '{mapping_file}' is invalid: "
                    f"tripleMaps[{triple_map_index}].predicateObjectMaps[{predicate_object_map_index}].objectMap "
                    "uses rdf:langString but does not define a language tag via 'lang'."
                )

        for value, context, is_template in values_to_check:
            if _is_prefixed_iri(value):
                prefix_name = value.split(":", 1)[0]
                if prefix_name not in prefixes:
                    raise Exception(
                        f"The mapping file at '{mapping_file}' contains an undefined prefix '{prefix_name}' in {context}."
                    )
                if is_template:
                    _validate_prefixed_iri(value, context, mapping_file)
                else:
                    _validate_prefixed_iri(value, context, mapping_file)


def load_and_validate_mapping(mapping_file: str | os.PathLike) -> dict:
    """Load JSON mapping file."""
    mapping = None
    try:
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping = json.load(f)
    except UnicodeDecodeError as e:
        raise Exception(f"The file at '{mapping_file}' could not be decoded. Please ensure the file is saved with UTF-8 encoding.")
    except json.JSONDecodeError as e:
        raise Exception(f"The file at '{mapping_file}' is not valid JSON.\nDetails: {e.msg}")
    try:
        with open(os.path.abspath(os.path.dirname(os.path.abspath(__file__))) + '/../schema/mapping-schema.json') as f:
            validate(instance=mapping, schema=json.load(f))
    except ValidationError as e:
        raise Exception(f"The mapping file at '{mapping_file}' is not valid according to the schema.\nDetails: {e.message}")

    _validate_prefixes(mapping.get("prefixes", {}), mapping_file)
    _validate_mapping_references(mapping, mapping_file)

    return mapping


def write_full_turtle_iri(lexical_form_parts: list[str], writer: TextIO) -> None:
    """Write a full URI in Turtle format, escaping special characters."""
    assert(len(lexical_form_parts) > 0 and lexical_form_parts[0][0] == '<' and lexical_form_parts[-1][-1] == '>')
    for (part_index, part) in enumerate(lexical_form_parts):
        if part_index % 2 == 0:
            # This is a static part of the template; escape special characters with \uXXXX as needed
            for (character_index, character) in enumerate(part):
                if (part_index == 0 and character_index == 0) or (part_index == len(lexical_form_parts) - 1 and character_index == len(part) - 1):
                    writer.write(character)
                elif ord(character) <= 0x20 or character in ['<', '>', '"', '{', '}', '|', '^', '`', '\\']:
                    writer.write(f"\\u{ord(character):04X}")
                else:
                    writer.write(character)
        else:
            # This is a dynamic part of the template; escape special characters using percent-encoding
            writer.write(urllib.parse.quote(part, safe=''))

def write_prefixed_turtle_iri(lexical_form_parts: list[str], writer: TextIO) -> None:
    """Write a URI in Turtle format using prefixes where possible."""
    for (part_index, part) in enumerate(lexical_form_parts):
        if part_index % 2 == 0:
            # This is a static part of the template. For prefixed URIs, there is no escaping of special characters in the static parts.
            writer.write(part)
        else:
            # This is a dynamic part of the template; escape special characters using percent-encoding

            # Prefixed IRIs must not end with a dot. Terminating dots at the end of the value template for a prefixed IRI should be
            # validated out when the mapping is loaded but we still need to handle the case where the final lexical form ends with a dot
            # due to a value substitution. We do so by percent-encoding the final dot as "%2E".
            if lexical_form_parts[-1] == "" and part_index == len(lexical_form_parts) - 2 and lexical_form_parts[part_index][-1] == '.':
                writer.write(urllib.parse.quote(part[:-1], safe=''))
                writer.write("%2E")
            else:
                writer.write(urllib.parse.quote(part, safe=''))


def write_turtle_iri(lexical_form_parts: list[str], writer: TextIO) -> None:
    """Write a URI in Turtle format, escaping special characters."""
    if lexical_form_parts[0][0] == "<":
        assert lexical_form_parts[-1][-1] == ">"
        write_full_turtle_iri(lexical_form_parts, writer)
    else:
        write_prefixed_turtle_iri(lexical_form_parts, writer)


# All C0 control characters → \u00XX
TRANSLATION_MAP = {}
for i in range(0x20):
    TRANSLATION_MAP[i] = f"\\u{i:04X}"
TRANSLATION_MAP[ord("\b")] = "\\b"
TRANSLATION_MAP[ord("\t")] = "\\t"
TRANSLATION_MAP[ord("\n")] = "\\n"
TRANSLATION_MAP[ord("\f")] = "\\f"
TRANSLATION_MAP[ord("\r")] = "\\r"
TRANSLATION_MAP[ord("\"")] = "\\\""
TRANSLATION_MAP[ord("\\")] = "\\\\"


def write_turtle_literal(lexical_form_parts: [str], datatype: str, writer: TextIO, **kwargs) -> None:
    """Write a literal in Turtle format, escaping special characters."""
    writer.write("\"")
    for (part_index, part) in enumerate(lexical_form_parts):
        writer.write(part.translate(TRANSLATION_MAP))
    writer.write("\"")
    if datatype == "rdf:langString" or datatype == "<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>":
        if "lang" not in kwargs:
            raise Exception("A language tag must be provided for rdf:langString literals.")
        lang = kwargs.get("lang")
        writer.write(f"@{lang}")
    else:
        writer.write(f"^^")
        write_turtle_iri([datatype], writer)


def write_turtle_resource(lexical_form_parts: [str], type: str, writer: TextIO, **kwargs) -> None:
    if type == "IRI":
        write_turtle_iri(lexical_form_parts, writer)
    else:
        write_turtle_literal(lexical_form_parts, type, writer, **kwargs)


def calculate_lexical_form_parts(lexical_form_template: str, row_data: pd.Series, row_index: int) -> list[str] | None:
    """Replace placeholders in a template using row values."""
    parts = re.split(r"\{([^{}]+)}", lexical_form_template)
    for (i, part) in enumerate(parts):
        if i % 2 == 1:
            if part == "row#":
                parts[i] = str(row_index)
            elif part in row_data:
                if pd.isnull(row_data[part]):
                    return None
                else:
                    parts[i] = str(row_data[part])
            else:
                ex = KeyError("The template '" + lexical_form_template + "' refers to a column '" + part + "' which is not present in the supplied table. The table has the following columns: '" + str(row_data.keys()) + "'.")
                ex.add_note(f"Column '{part}' not found in row.")
                raise ex
    return parts


def write_turtle_for_row(row_data: pd.Series, row_index: int, mapping: dict, writer: TextIO) -> None:
    for structure in mapping["tripleMaps"]:
        subject_lexical_form_parts = calculate_lexical_form_parts(structure["subjectMap"]["value"], row_data, row_index)
        if subject_lexical_form_parts is not None:
            at_least_one_triple_for_current_subject = False
            # Add all subject classes
            for cls in structure["subjectMap"].get("classes", []):
                if not at_least_one_triple_for_current_subject:
                    at_least_one_triple_for_current_subject = True
                    write_turtle_iri(subject_lexical_form_parts, writer)
                    writer.write(" a ")
                else:
                    writer.write(", ")
                write_turtle_iri([cls], writer)
            # Process all predicate-object mappings for the current subject
            object_class_map = {}
            for predicate_object_map in structure["predicateObjectMaps"]:
                at_least_one_object_for_current_subject_and_predicate = False
                predicate = predicate_object_map["predicate"]
                object_type = predicate_object_map["objectMap"]["type"]
                language = predicate_object_map["objectMap"]["lang"] if "lang" in predicate_object_map["objectMap"] else None
                object_lexical_form_parts = calculate_lexical_form_parts(predicate_object_map["objectMap"]["value"], row_data, row_index)
                if object_lexical_form_parts is not None:
                    if not at_least_one_triple_for_current_subject:
                        at_least_one_triple_for_current_subject = True
                        write_turtle_iri(subject_lexical_form_parts, writer)
                        writer.write(" ")
                    else:
                        writer.write(" ;\n    ")
                    if not at_least_one_object_for_current_subject_and_predicate:
                        at_least_one_object_for_current_subject_and_predicate = True
                        write_turtle_iri([predicate], writer)
                    else:
                        writer.write(",")
                    writer.write(" ")
                    buffer = io.StringIO()
                    write_turtle_resource(object_lexical_form_parts, object_type, buffer, lang=language)
                    writer.write(buffer.getvalue())
                    # Record object classes to write out after we've finished all triples for this subject
                    if object_type == "IRI" and "classes" in predicate_object_map["objectMap"]:
                        object_iri = buffer.getvalue()
                        for cls in predicate_object_map["objectMap"]["classes"]:
                            if object_iri not in object_class_map:
                                object_class_map[object_iri] = []
                            object_class_map[object_iri].append(cls)
            if at_least_one_triple_for_current_subject:
                writer.write(" .\n\n")
            # Write out object class assertions
            for object_iri, classes in object_class_map.items():
                writer.write(object_iri)
                writer.write(" a ")
                for i, cls in enumerate(classes):
                    if i > 0:
                        writer.write(", ")
                    write_turtle_iri([cls], writer)
                writer.write(" .\n\n")


def write_turtle_for_dataframe(dataframe: pd.DataFrame, mapping: dict, row_index_offset: int, writer: TextIO) -> None:
    """Convert a pandas data frame to Turtle according to the specified mapping and write it to the writer."""
    for row_index, row in dataframe.iterrows():
        write_turtle_for_row(row, row_index_offset + row_index + 1, mapping, writer)

def initialize_turtle_file(data_file: str | os.PathLike, mapping: dict, writer: TextIO) -> None:
    """Write header comments and prefixes."""
    writer.write("# Schema version: {}\n".format(mapping.get("schemaVersion", "unknown")))
    writer.write("# Source file: {}\n".format(data_file))
    writer.write("# MappingID: {}\n".format(mapping.get("mappingID", None)))
    writer.write("# MappingVersion: {}\n".format(mapping.get("mappingVersion", "unknown")))
    if 'mappingName' in mapping:
        writer.write("# MappingName: {}\n".format(mapping.get("mappingName", None)))
    writer.write("\n")
    writer.write("\n")
    for prefix, iri in mapping.get("prefixes", {}).items():
        writer.write(f"@prefix {prefix}: ")
        write_full_turtle_iri([iri], writer)
        writer.write(" .\n")
    writer.write("\n")
    writer.flush()


def convert_csv_to_rdf(csv_file: str | os.PathLike, mapping_file: str | os.PathLike, output_ttl: str | os.PathLike, batch_size=BATCH_SIZE) -> None:
    """Convert CSV data to RDF while periodically flushing output."""
    mapping = load_and_validate_mapping(mapping_file)
    temporary_output_ttl = output_ttl + ".temp"
    writer = open(temporary_output_ttl, "w", encoding="utf-8")
    try:
        initialize_turtle_file(csv_file,mapping, writer)
        # Read CSV in chunks
        with pd.read_csv(csv_file, chunksize=batch_size, dtype="str") as reader:
            for df in reader:
                # Unlike Parquet, the data frames returned by the CSV reader already have the correct row indices so the offset is always 0.
                write_turtle_for_dataframe(df, mapping, 0, writer)
                writer.flush()
    except Exception as e:
        writer.close()
        os.remove(temporary_output_ttl)
        raise e
    writer.close()
    os.rename(temporary_output_ttl, output_ttl)


def convert_parquet_to_rdf(parquet_file: str | os.PathLike, mapping_file: str | os.PathLike, output_ttl: str | os.PathLike, batch_size=BATCH_SIZE) -> None:
    """Convert Parquet data to RDF while periodically flushing output."""
    mapping = load_and_validate_mapping(mapping_file)
    temporary_output_ttl = output_ttl + ".temp"
    writer = open(temporary_output_ttl, "w", encoding="utf-8")
    try:
        initialize_turtle_file(parquet_file, mapping, writer)
        # Read Parquet in chunks
        parquet_file = pq.ParquetFile(parquet_file)
        row_index_offset = 0
        for batch in parquet_file.iter_batches(batch_size):
            df = batch.to_pandas()
            write_turtle_for_dataframe(df, mapping, row_index_offset, writer)
            writer.flush()
            row_index_offset += batch.num_rows
    except Exception as e:
        writer.close()
        os.remove(temporary_output_ttl)
        raise e
    writer.close()
    os.rename(temporary_output_ttl, output_ttl)
