
import io
from unittest import TestCase
from data_mapping import convert_csv_to_rdf, convert_parquet_to_rdf
from data_mapping.data_mapping import initialize_turtle_file, write_turtle_for_dataframe, load_and_validate_mapping
import json
import pandas as pd
from jsonschema import validate
import os
import subprocess
import tempfile
import textwrap
from string import Template

RDFOX_DIFF_GRAPHS_SCRIPT = Template("""
dstore create default

dsprop set invalid-literal-policy $invalid_literal_policy

import > <expected> $expected_ttl_file_name
import > <actual> $actual_ttl_file_name

set query.answer-format text/tab-separated-values

set output in_expected_only.ttl
construct { ?s ?p ?o } where { graph <expected> { ?s ?p ?o } . filter not exists { graph <actual> { ?s ?p ?o } } } limit 100

set output in_actual_only.ttl
construct { ?s ?p ?o } where { graph <actual> { ?s ?p ?o } . filter not exists { graph <expected> { ?s ?p ?o } } } limit 100
""")

def remove_file_if_exists(filename: str):
    """Remove a file if it exists. Ignore if it does not exist."""
    try:
        os.remove(filename)
    except FileNotFoundError:
        pass

class ErrorPathTests(TestCase):
    """These tests use invalid inputs and verify that appropriate exceptions are raised."""

    @staticmethod
    def _base_mapping() -> dict:
        return {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "prefixes": {
                "ex": "<http://example.com/>"
            },
            "tripleMaps": [
                {
                    "subjectMap": {
                        "value": "ex:person/{ID}",
                        "type": "IRI"
                    },
                    "predicateObjectMaps": [
                        {
                            "predicate": "ex:name",
                            "objectMap": {
                                "value": "{Name}",
                                "type": "<http://www.w3.org/2001/XMLSchema#string>"
                            }
                        }
                    ]
                }
            ]
        }

    def _assert_mapping_validation_error(self, mapping: dict, expected_error_fragment: str):
        with tempfile.TemporaryDirectory() as temporary_directory_path:
            mapping_file_path = os.path.join(temporary_directory_path, "mapping.json")
            with open(mapping_file_path, "w", encoding="utf-8") as f:
                json.dump(mapping, f)
            with self.assertRaises(Exception) as context:
                load_and_validate_mapping(mapping_file_path)
            self.assertIn(expected_error_fragment, str(context.exception))

    def test_reference_to_missing_column(self):
        with self.assertRaises(KeyError):
            mapping = {
                "schemaVersion": "0.2.0",
                "mappingVersion": "1.0.0",
                "mappingID": "abc123",
                "mappingName": "Person",
                "tripleMaps": [
                    {
                        "subjectMap": {
                            "value": "<http://example.com/person/{ID}>",
                            "type": "IRI"
                        },
                        "predicateObjectMaps": [
                            {
                                "predicate": "<http://xmlns.com/foaf/0.1/name>",
                                "objectMap": {
                                    "value": "{FirstName} {LastName}",
                                    "type": "<http://www.w3.org/2001/XMLSchema#string>"
                                }
                            }
                        ]
                    }
                ]
            }
            df = pd.DataFrame({
                "ID": [1],
                "FirstName": ["Alice"]
                # "LastName" column is missing
            })
            write_turtle_for_dataframe(df, mapping, 0, io.StringIO())

    def test_invalid_prefix_name(self):
        mapping = self._base_mapping()
        mapping["prefixes"] = {
            "bad prefix": "<http://example.com/>"
        }
        self._assert_mapping_validation_error(mapping, "invalid prefix name")

    def test_invalid_prefix_iri_declaration(self):
        mapping = self._base_mapping()
        mapping["prefixes"] = {
            "ex": "http://example.com/"
        }
        self._assert_mapping_validation_error(mapping, "Prefix IRIs must start with '<' and end with '>'")

    def test_undefined_prefix_reference(self):
        mapping = self._base_mapping()
        mapping["tripleMaps"][0]["predicateObjectMaps"][0]["predicate"] = "missing:name"
        self._assert_mapping_validation_error(mapping, "undefined prefix 'missing'")

    def test_unescapable_characters_in_prefixed_iri(self):
        mapping = self._base_mapping()
        mapping["tripleMaps"][0]["subjectMap"]["value"] = "ex:person with spaces/{ID}"
        self._assert_mapping_validation_error(mapping, "invalid prefixed IRI")

    def test_dot_at_end_of_prefixed_iri(self):
        mapping = self._base_mapping()
        mapping["tripleMaps"][0]["subjectMap"]["value"] = "ex:person."
        self._assert_mapping_validation_error(mapping, "must not end with a dot")

    def test_missing_language_tag_for_langstring(self):
        mapping = self._base_mapping()
        mapping["tripleMaps"][0]["predicateObjectMaps"][0]["objectMap"] = {
            "value": "{Name}",
            "type": "rdf:langString"
        }
        mapping["prefixes"]["rdf"] = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#>"
        self._assert_mapping_validation_error(mapping, "uses rdf:langString but does not define a language tag")

def assert_graphs_equal(working_directory_path: str, expected_ttl_file_name: str, actual_ttl_file_names: list[str], invalid_literals_expected: bool = False) -> tuple[bool, str]:
    script_file = os.path.join(working_directory_path, "diff.rdfox")
    remove_file_if_exists(script_file)
    in_expected_only_file = os.path.join(working_directory_path, "in_expected_only.ttl")
    remove_file_if_exists(in_expected_only_file)
    in_actual_only_file = os.path.join(working_directory_path, "in_actual_only.ttl")
    remove_file_if_exists(in_actual_only_file)
    invalid_literal_policy = "as-string-silent" if invalid_literals_expected else "error"
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(RDFOX_DIFF_GRAPHS_SCRIPT.substitute(expected_ttl_file_name=expected_ttl_file_name, actual_ttl_file_name=" ".join(actual_ttl_file_names), invalid_literal_policy=invalid_literal_policy))

    process = subprocess.run(["RDFox", "sandbox", working_directory_path, "set on-error stop", "diff", "quit"], check=False, capture_output=True)
    if process.returncode != 0:
        message = f"RDFox process failed with return code {process.returncode}.\n\n"
        message += f"The complete output was:\n{textwrap.indent(process.stdout.decode('utf-8'), '    ')}\n"
        return False, message
    else:
        message = ""
        with open(in_expected_only_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) > 1:
                message += "\nAt least these triples were expected but not present:\n\n"
                for line in lines[1:]:
                    message += "    " + line
        with open(in_actual_only_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) > 1:
                message += "\nAt least these triples were present but not expected:\n\n"
                for line in lines[1:]:
                    message += "    " + line
        if message != "":
            return False, message
    return True, ""

class InlineMappingTests(TestCase):
    """These tests use only valid inputs and verify that the outputs are as expected."""

    def _do_mapping_test(self, mapping_json: str, df: pd.DataFrame, expected_graph_ttl: str):
        mapping = json.loads(mapping_json)

        # Sanity check to ensure that all tests are using valid mappings.
        with open(os.path.abspath(os.path.dirname(os.path.abspath(__file__))) + '/../schema/mapping-schema.json') as f:
            validate(instance=mapping, schema=json.load(f))

        with tempfile.TemporaryDirectory() as temporary_directory_path:
            expected_ttl_file_path = os.path.join(temporary_directory_path, "expected.ttl")
            actual_ttl_file_path = os.path.join(temporary_directory_path, "actual.ttl")
            with open(expected_ttl_file_path, "w", encoding="utf-8") as writer:
                writer.write(expected_graph_ttl)
            writer = io.StringIO()
            initialize_turtle_file("[dummy path]", mapping, writer)
            write_turtle_for_dataframe(df, mapping, 0, writer)
            actual_graph_ttl = writer.getvalue()
            with open(actual_ttl_file_path, "w", encoding="utf-8") as f:
                f.write(actual_graph_ttl)
            assertion_passed, message = assert_graphs_equal(temporary_directory_path, "expected.ttl", ["actual.ttl"])
            if not assertion_passed:
                message += f"The expected graph was:\n\n{expected_graph_ttl}\n"
                message += f"The actual graph was:\n\n{actual_graph_ttl}\n"
                self.fail(message)

    # Tests start here

    def test_iri_escaping(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "prefixes": {
                "ex": "<http://example.com/<>\\\"|^`\\\\/>",
                "xsd": "<http://www.w3.org/2001/XMLSchema#>",
                "foaf": "<http://xmlns.com/foaf/0.1/>"
            },
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "<http://example.com/<>\\\"|^`\\\\/{ID}>",
                  "type": "IRI",
                  "classes": [ "ex:Person" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "foaf:name",
                    "objectMap": {
                      "value": "{Name}",
                      "type": "xsd:string"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": ["1 1"],
            "Name": ["Alice Smith"]
        })
        expected_graph_ttl = """
          @prefix ex: <http://example.com/\\u003C\\u003E\\u0022\\u007C\\u005E\\u0060\\u005C/> .
          @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
          @prefix foaf: <http://xmlns.com/foaf/0.1/> .

          <http://example.com/\\u003C\\u003E\\u0022\\u007C\\u005E\\u0060\\u005C/1%201> a ex:Person ;
              foaf:name "Alice Smith" .
          """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)


    def test_prefixed_iri_escaping(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "prefixes": {
                "ex": "<http://example.com/<>\\\"|^`\\\\/>",
                "xsd": "<http://www.w3.org/2001/XMLSchema#>",
                "foaf": "<http://xmlns.com/foaf/0.1/>"
            },
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "<http://example.com/<>\\\"|^`\\\\/{ID}>",
                  "type": "IRI",
                  "classes": [ "ex:Person" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "foaf:name",
                    "objectMap": {
                      "value": "{Name}",
                      "type": "xsd:string"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": ["1 1"],
            "Name": ["Alice Smith"]
        })
        expected_graph_ttl = """
          @prefix ex: <http://example.com/\\u003C\\u003E\\u0022\\u007C\\u005E\\u0060\\u005C/> .
          @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
          @prefix foaf: <http://xmlns.com/foaf/0.1/> .

          ex:1%201 a ex:Person ;
              foaf:name "Alice Smith" .
          """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_literal_escaping(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "prefixes": {
                "": "<http://example.com/vocabulary/>",
                "people": "<http://example.com/people/>",
                "xsd": "<http://www.w3.org/2001/XMLSchema#>",
                "foaf": "<http://xmlns.com/foaf/0.1/>"
            },
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "people:{ID}",
                  "type": "IRI",
                  "classes": [ ":Person" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "foaf:name",
                    "objectMap": {
                      "value": "\\b\\f\\n\\r\\t_\\\\_\\\"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\u000B\\u000E\\u000F\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001A\\u001B\\u001C\\u001D\\u001E\\u001F {Name}",
                      "type": "xsd:string"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": ["1"],
            "Name": ["\b\f\n\r\t_\\_\"\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\u000B\u000E\u000F\u0010\u0011\u0012\u0013\u0014\u0015\u0016\u0017\u0018\u0019\u001A\u001B\u001C\u001D\u001E\u001F"]
        })
        expected_graph_ttl = """
          @prefix : <http://example.com/vocabulary/> .
          @prefix people: <http://example.com/people/> .
          @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
          @prefix foaf: <http://xmlns.com/foaf/0.1/> .

          people:1 a :Person ;
              foaf:name "\\b\\f\\n\\r\\t_\\\\_\\"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\u000B\\u000E\\u000F\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001A\\u001B\\u001C\\u001D\\u001E\\u001F \\b\\f\\n\\r\\t_\\\\_\\"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\u000B\\u000E\\u000F\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001A\\u001B\\u001C\\u001D\\u001E\\u001F" .
          """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_with_rdf_langstring(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "<http://example.com/person/{ID}>",
                  "type": "IRI"
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{FirstName} {LastName}",
                      "type": "<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>",
                      "lang": "en"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": [1],
            "FirstName": ["Alice"],
            "LastName": ["Smith"]
        })
        expected_graph_ttl = "<http://example.com/person/1> <http://xmlns.com/foaf/0.1/name> \"Alice Smith\"@en ."
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_with_full_iris(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "<http://example.com/person/{ID}>",
                  "type": "IRI",
                  "classes": [ "<http://example.com/Person>", "<http://example.com/Employee>" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{FirstName} {LastName}",
                      "type": "<http://www.w3.org/2001/XMLSchema#string>"
                    }
                  },
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{FirstName} {LastName}",
                      "type": "<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>",
                      "lang": "pl"
                    }
                  },
                  {
                    "predicate": "<http://example.com/worksFor>",
                    "objectMap": {
                      "value": "<http://example.com/company with spaces/{CompanyID}>",
                      "type": "IRI",
                      "classes": [ "<http://example.com/Company>", "<http://example.com/Organization>" ]
                    }
                  },
                  {
                    "predicate": "<http://example.com/employedOn>",
                    "objectMap": {
                      "value": "{Year}-01-01",
                      "type": "<http://www.w3.org/2001/XMLSchema#date>"
                    }
                  }
                ]
              },
              {
                "subjectMap": {
                  "value": "<http://example.com/company with spaces/{CompanyID}>",
                  "type": "IRI",
                  "classes": [ "<http://example.com/Company>" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{CompanyName}",
                      "type": "<http://www.w3.org/2001/XMLSchema#string>"
                    }
                  },
                  {
                    "predicate": "<http://example.com/referencedInContext>",
                    "objectMap": {
                      "value": "{row#}",
                      "type": "<http://www.w3.org/2001/XMLSchema#int>"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": [1, 2],
            "FirstName": ["Alice", "Jane"],
            "LastName": ["Smith", "Kelly"],
            "CompanyID": [100, 101],
            "CompanyName": ["OST", "Samsung Electronics"],
            "Year": [2021, 2022]
        })
        expected_graph_ttl = """
            <http://example.com/person/1> <http://example.com/employedOn> "2021-01-01"^^<http://www.w3.org/2001/XMLSchema#date> ;
                <http://xmlns.com/foaf/0.1/name> "Alice Smith" , "Alice Smith"@pl ;
                <http://example.com/worksFor> <http://example.com/company\\u0020with\\u0020spaces/100> ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Person> , <http://example.com/Employee> .

            <http://example.com/company\\u0020with\\u0020spaces/100> <http://example.com/referencedInContext> "1"^^<http://www.w3.org/2001/XMLSchema#int> ;
                <http://xmlns.com/foaf/0.1/name> "OST" ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Company> , <http://example.com/Organization> .

            <http://example.com/person/2> <http://example.com/employedOn> "2022-01-01"^^<http://www.w3.org/2001/XMLSchema#date> ;
                <http://xmlns.com/foaf/0.1/name> "Jane Kelly" , "Jane Kelly"@pl ;
                <http://example.com/worksFor> <http://example.com/company\\u0020with\\u0020spaces/101> ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Person> , <http://example.com/Employee> .

            <http://example.com/company\\u0020with\\u0020spaces/101> <http://example.com/referencedInContext> "2"^^<http://www.w3.org/2001/XMLSchema#int> ;
                <http://xmlns.com/foaf/0.1/name> "Samsung Electronics" ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Company> , <http://example.com/Organization> .
        """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_with_prefixed_iris(self):
        mapping_json = """
            {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "prefixes": {
                "companies": "<http://example.com/companies/>",
                "ex": "<http://example.com/>",
                "foaf": "<http://xmlns.com/foaf/0.1/>",
                "people": "<http://example.com/people/>",
                "rdf": "<http://www.w3.org/1999/02/22-rdf-syntax-ns#>",
                "xsd": "<http://www.w3.org/2001/XMLSchema#>"
            },
            "tripleMaps": [
                {
                "subjectMap": {
                    "value": "people:{ID}",
                    "type": "IRI",
                    "classes": [ "ex:Person", "ex:Employee" ]
                },
                "predicateObjectMaps": [
                    {
                    "predicate": "foaf:name",
                    "objectMap": {
                        "value": "{FirstName} {LastName}",
                        "type": "xsd:string"
                    }
                    },
                    {
                    "predicate": "foaf:name",
                    "objectMap": {
                        "value": "{FirstName} {LastName}",
                        "type": "rdf:langString",
                        "lang": "pl"
                    }
                    },
                    {
                    "predicate": "ex:worksFor",
                    "objectMap": {
                        "value": "companies:{CompanyID}",
                        "type": "IRI",
                        "classes": [ "ex:Company", "ex:Organization" ]
                    }
                    },
                    {
                    "predicate": "ex:employedOn",
                    "objectMap": {
                        "value": "{Year}-01-01",
                        "type": "xsd:date"
                    }
                    }
                ]
                },
                {
                "subjectMap": {
                    "value": "companies:{CompanyID}",
                    "type": "IRI",
                    "classes": [ "ex:Company" ]
                },
                "predicateObjectMaps": [
                    {
                    "predicate": "foaf:name",
                    "objectMap": {
                        "value": "{CompanyName}",
                        "type": "xsd:string"
                    }
                    },
                    {
                    "predicate": "ex:referencedInContext",
                    "objectMap": {
                        "value": "{row#}",
                        "type": "xsd:int"
                    }
                    }
                ]
                }
            ]
            }"""
        df = pd.DataFrame({
            "ID": [1, 2],
            "FirstName": ["Alice", "Jane"],
            "LastName": ["Smith", "Kelly"],
            "CompanyID": [100, 101],
            "CompanyName": ["OST", "Samsung Electronics"],
            "Year": [2021, 2022]
        })
        expected_graph_ttl = """
            @prefix companies: <http://example.com/companies/> .
            @prefix ex: <http://example.com/> .
            @prefix foaf: <http://xmlns.com/foaf/0.1/> .
            @prefix people: <http://example.com/people/> .
            @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            people:1 a ex:Person, ex:Employee;
                ex:worksFor companies:100;
                foaf:name "Alice Smith"^^xsd:string;
                foaf:name "Alice Smith"@pl;
                ex:employedOn "2021-01-01"^^xsd:date.
            companies:100 a ex:Company, ex:Organization;
                foaf:name "OST"^^xsd:string;
                ex:referencedInContext "1"^^xsd:int .
            people:2 a ex:Person, ex:Employee;
                ex:worksFor companies:101;
                foaf:name "Jane Kelly"^^xsd:string;
                foaf:name "Jane Kelly"@pl;
                ex:employedOn "2022-01-01"^^xsd:date.
            companies:101 a ex:Company, ex:Organization;
                foaf:name "Samsung Electronics"^^xsd:string;
                ex:referencedInContext "2"^^xsd:int .
        """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_row_numbering(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "<http://example.com/person/{ID}>",
                  "type": "IRI",
                  "classes": [ "<http://example.com/Person>", "<http://example.com/Employee>" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{FirstName} {LastName}",
                      "type": "<http://www.w3.org/2001/XMLSchema#string>"
                    }
                  },
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{FirstName} {LastName}",
                      "type": "<http://www.w3.org/1999/02/22-rdf-syntax-ns#langString>",
                      "lang": "pl"
                    }
                  },
                  {
                    "predicate": "<http://example.com/worksFor>",
                    "objectMap": {
                      "value": "<http://example.com/company with spaces/{CompanyID}>",
                      "type": "IRI",
                      "classes": [ "<http://example.com/Company>", "<http://example.com/Organization>" ]
                    }
                  },
                  {
                    "predicate": "<http://example.com/employedOn>",
                    "objectMap": {
                      "value": "{Year}-01-01",
                      "type": "<http://www.w3.org/2001/XMLSchema#date>"
                    }
                  }
                ]
              },
              {
                "subjectMap": {
                  "value": "<http://example.com/company with spaces/{CompanyID}>",
                  "type": "IRI",
                  "classes": [ "<http://example.com/Company>" ]
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "<http://xmlns.com/foaf/0.1/name>",
                    "objectMap": {
                      "value": "{CompanyName}",
                      "type": "<http://www.w3.org/2001/XMLSchema#string>"
                    }
                  },
                  {
                    "predicate": "<http://example.com/referencedInContext>",
                    "objectMap": {
                      "value": "{row#}",
                      "type": "<http://www.w3.org/2001/XMLSchema#int>"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": [1, 2],
            "FirstName": ["Alice", "Jane"],
            "LastName": ["Smith", "Kelly"],
            "CompanyID": [100, 101],
            "CompanyName": ["OST", "Samsung Electronics"],
            "Year": [2021, 2022]
        })
        expected_graph_ttl = """
            <http://example.com/person/1> <http://example.com/employedOn> "2021-01-01"^^<http://www.w3.org/2001/XMLSchema#date> ;
                <http://xmlns.com/foaf/0.1/name> "Alice Smith" , "Alice Smith"@pl ;
                <http://example.com/worksFor> <http://example.com/company\\u0020with\\u0020spaces/100> ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Person> , <http://example.com/Employee> .

            <http://example.com/company\\u0020with\\u0020spaces/100> <http://example.com/referencedInContext> "1"^^<http://www.w3.org/2001/XMLSchema#int> ;
                <http://xmlns.com/foaf/0.1/name> "OST" ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Company> , <http://example.com/Organization> .

            <http://example.com/person/2> <http://example.com/employedOn> "2022-01-01"^^<http://www.w3.org/2001/XMLSchema#date> ;
                <http://xmlns.com/foaf/0.1/name> "Jane Kelly" , "Jane Kelly"@pl ;
                <http://example.com/worksFor> <http://example.com/company\\u0020with\\u0020spaces/101> ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Person> , <http://example.com/Employee> .

            <http://example.com/company\\u0020with\\u0020spaces/101> <http://example.com/referencedInContext> "2"^^<http://www.w3.org/2001/XMLSchema#int> ;
                <http://xmlns.com/foaf/0.1/name> "Samsung Electronics" ;
                <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.com/Company> , <http://example.com/Organization> .
        """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

    def test_with_dot_at_end_of_prefixed_iri(self):
        mapping_json = """
          {
            "schemaVersion": "0.2.0",
            "mappingVersion": "1.0.0",
            "mappingID": "abc123",
            "mappingName": "Person",
            "prefixes": {
                "ex": "<http://example.com/>",
                "person": "<http://example.com/person/>",
                "thing": "<http://example.com/thing/>"
            },
            "tripleMaps": [
              {
                "subjectMap": {
                  "value": "person:{ID}",
                  "type": "IRI"
                },
                "predicateObjectMaps": [
                  {
                    "predicate": "ex:relatedThing",
                    "objectMap": {
                      "value": "thing:{ThingID}-",
                      "type": "IRI"
                    }
                  }
                ]
              }
            ]
          }"""
        df = pd.DataFrame({
            "ID": ["1."],
            "ThingID": ["A."]
        })
        expected_graph_ttl = """
        @prefix ex: <http://example.com/> .
        @prefix person: <http://example.com/person/> .
        @prefix thing: <http://example.com/thing/> .

        person:1%2E ex:relatedThing thing:A.- .
        """
        self._do_mapping_test(mapping_json, df, expected_graph_ttl)

from collections import namedtuple

FileBasedMappingTask = namedtuple("FileBasedMappingTask", "data_file_name mapping_file_name batch_size invalid_literals_expected")

class FileBasedMappingTests(TestCase):

    def _do_file_based_mapping_test(self, example_name: str, mapping_tasks: list[FileBasedMappingTask]):
        directory_path = "examples/" + example_name
        actual_ttl_file_names = []
        for task_index, task in enumerate(mapping_tasks):
          data_file_name_and_extension = os.path.splitext(task.data_file_name)
          data_file_name_stem = data_file_name_and_extension[0]
          data_file_extension = data_file_name_and_extension[-1].lower()
          data_file_path = os.path.join(directory_path, task.data_file_name)
          mapping_file_path = os.path.join(directory_path, task.mapping_file_name)
          actual_ttl_file_name = f"{data_file_name_stem}.ttl"
          remove_file_if_exists(actual_ttl_file_name)
          actual_ttl_file_names.append(actual_ttl_file_name)
          actual_ttl_file_path = os.path.join(directory_path, actual_ttl_file_name)
          if data_file_extension == ".parquet":
            convert_parquet_to_rdf(data_file_path, mapping_file_path, actual_ttl_file_path, task.batch_size)
          elif data_file_extension == ".csv":
            convert_csv_to_rdf(data_file_path, mapping_file_path, actual_ttl_file_path, task.batch_size)
          else:
            raise ValueError(f"Unsupported data format: {data_file_extension}")
        passed, message = assert_graphs_equal(directory_path, "reference_output.ttl", actual_ttl_file_names, task.invalid_literals_expected)
        if not passed:
            self.fail(message)

    def test_employment_example(self):
        self._do_file_based_mapping_test("employment", [FileBasedMappingTask("data.parquet", "mapping.json", 1000, False)])

    # Reference data for flight example is too large to include in repo.
    # def test_flight_example(self):
    #     self._do_file_based_mapping_test("flight", [FileBasedMappingTask("f1m.parquet", "mapping.json", 1000, True)])

    def test_hr_example(self):
        departments = FileBasedMappingTask("departments.csv", "departments-mapping.json", 1000, False)
        employees = FileBasedMappingTask("employees.csv", "employees-mapping.json", 1000, False)
        self._do_file_based_mapping_test("hr", [departments, employees])

    def test_northwind_example(self):
      self._do_file_based_mapping_test("northwind", [FileBasedMappingTask("orderDetails.csv", "orderDetails.json", 50, True)])
      self._do_file_based_mapping_test("northwind", [FileBasedMappingTask("orderDetails.csv", "orderDetails.json", 500, True)])

    def test_people_example(self):
        self._do_file_based_mapping_test("people", [FileBasedMappingTask("data.csv", "mapping.json", 1000, False)])

    def test_titanic_example(self):
        # The Titanic example has 891 rows, so we use a smaller batch size to test row-indexing works across batches.
        self._do_file_based_mapping_test("titanic", [FileBasedMappingTask("data.parquet", "mapping.json", 183, False)])
        self._do_file_based_mapping_test("titanic", [FileBasedMappingTask("data.parquet", "mapping.json", 300, False)])

    def test_transactions_example(self):
        self._do_file_based_mapping_test("transactions", [FileBasedMappingTask("data.csv", "mapping.json", 1000, False)])
