#!/usr/bin/env python3
"""
OpenAPI Specification Evaluation Script

This script compares two OpenAPI specifications (ideal vs test) and generates
detailed Excel reports with precision and recall metrics for:
- Endpoints
- Parameters
- Constraints
- Response Status Codes
- Response Properties

The reports are saved in a 'comparison_results' folder inside the test spec's directory.
"""

import json
import yaml
import logging
import copy
import re
import os
import sys
import pandas as pd
from typing import Dict, List, Tuple, Set, Any
from openpyxl.styles import PatternFill

# --- SCRIPT SETUP ---

# Configure logging to a file
logging.basicConfig(
    filename="mathesar_openapi_comparison.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode='w' # Overwrite log file on each run
)
logger = logging.getLogger(__name__)

# --- PREPROCESSING FUNCTIONS ---

def remove_descriptions(data):
    if isinstance(data, list):
        for item in data: remove_descriptions(item)
    elif isinstance(data, dict):
        for key in ["description", "summary", "example", "tags"]:
            if key in data: del data[key]
        for value in data.values(): remove_descriptions(value)

def normalize_paths(data):
    if "paths" in data and isinstance(data["paths"], dict):
        paths, updated_paths = data["paths"], {}
        for path, methods in paths.items():
            norm_path = re.sub(r"^/api/v\d+/?", "/", path)
            norm_path = re.sub(r"^/api/?", "/", norm_path)
            norm_path = re.sub(r"\{[^}]+\}", "{placeholder}", norm_path)
            if not norm_path: norm_path = "/"
            elif not norm_path.startswith("/"): norm_path = "/" + norm_path
            updated_paths[norm_path] = methods
        data["paths"] = updated_paths

def preprocess_spec(spec: Dict) -> Dict:
    processed_spec = copy.deepcopy(spec)
    remove_descriptions(processed_spec)
    normalize_paths(processed_spec)
    return processed_spec

# --- CORE EVALUATOR CLASS ---

class OpenAPIEvaluator:
    """Evaluates and compares OpenAPI specifications."""
    
    def __init__(self, ideal_spec: Dict, test_spec: Dict):
        self.ideal_spec = ideal_spec
        self.test_spec = test_spec
        self.report_data = {}

    def resolve_ref(self, ref: str, spec: Dict) -> Dict:
        try:
            parts = ref.split('/')[1:]
            data = spec
            for part in parts: data = data[part]
            return data
        except (KeyError, TypeError, IndexError):
            logger.warning(f"Could not resolve $ref: {ref}")
            return {}
        
    def compare_endpoint_methods(self):
        ideal_paths = {p: set(m.upper() for m, v in meths.items() if isinstance(v, dict)) for p, meths in self.ideal_spec.get("paths", {}).items() if isinstance(meths, dict)}
        test_paths = {p: set(m.upper() for m, v in meths.items() if isinstance(v, dict)) for p, meths in self.test_spec.get("paths", {}).items() if isinstance(meths, dict)}
        all_paths = sorted(list(set(ideal_paths.keys()) | set(test_paths.keys())))
        
        report = []
        for path in all_paths:
            ideal_methods, test_methods = ideal_paths.get(path, set()), test_paths.get(path, set())
            report.append({ "endpoint_path": path, "correctly_identified_list": list(ideal_methods & test_methods), "missing_list": list(ideal_methods - test_methods), "extra_list": list(test_methods - ideal_methods) })
        self.report_data["Endpoint Methods"] = report


    def extract_parameters_from_schema(self, schema: Dict, location: str, spec: Dict, parent_key: str = "") -> List[Tuple[str, str, str]]:
        """Recursively extract parameters from a schema definition."""
        if not isinstance(schema, dict):
            return []
        
        parameters = []
        
        # Resolve $ref if present
        if "$ref" in schema:
            schema = self.resolve_ref(schema["$ref"], spec)
            if not schema:
                return []
        
        # Handle object type
        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            for param_name, param_schema in properties.items():
                if not isinstance(param_schema, dict):
                    continue
                
                full_param_name = f"{parent_key}.{param_name}" if parent_key else param_name
                param_type = param_schema.get("type", "unknown")
                
                # Add the parameter
                parameters.append((full_param_name, location, param_type))
                
                # Recurse for nested objects
                parameters.extend(
                        self.extract_parameters_from_schema(param_schema, location, spec, full_param_name)
                    )
        
        # Handle array type
        elif schema.get("type") == "array":
            array_param_name = parent_key if parent_key else "[]"
            parameters.append((array_param_name, location, "array"))
            items_schema = schema.get("items", {})
            if isinstance(items_schema, dict):
                array_key = f"{parent_key}[]" if parent_key else "[]"
                parameters.extend(
                    self.extract_parameters_from_schema(items_schema, location, spec, array_key)
                )
        
        # Handle oneOf, anyOf, allOf
        for schema_type in ["oneOf", "anyOf", "allOf"]:
            if schema_type in schema:
                for sub_schema in schema[schema_type]:
                    parameters.extend(
                        self.extract_parameters_from_schema(sub_schema, location, spec, parent_key)
                    )
        
        return parameters
    
    def _are_params_equivalent(self, ideal_param: Tuple[str, str, str, Any], test_param: Tuple[str, str, str, Any]) -> bool:
        """
        Helper function to determine if two parameters are equivalent.
        --- ADDED LOGGING FOR DEBUGGING ---
        """
        ideal_name, ideal_loc, _, ideal_pos = ideal_param
        test_name, test_loc, _, test_pos = test_param

        # --- LOGGING START ---
        print(f"\n--- Comparing ---\n  Ideal: {ideal_param}\n  Test:  {test_param}")
        # --- LOGGING END ---

        # Rule 1: Location must match.
        if ideal_loc != test_loc:
            # --- LOGGING START ---
            print(f"  -> RESULT: False (Location mismatch: '{ideal_loc}' != '{test_loc}')")
            # --- LOGGING END ---
            return False

        # Rule 2: For path parameters, match ONLY by position.
        if ideal_loc == 'path':
            print("  -> Logic Path: 'path' parameter")
            if ideal_pos is not None and test_pos is not None:
                result = ideal_pos == test_pos
                # --- LOGGING START ---
                print(f"  -> Comparing positions: {ideal_pos} == {test_pos} -> {result}")
                print(f"  -> RESULT: {result}")
                # --- LOGGING END ---
                return result
            else:
                # --- LOGGING START ---
                print(f"  -> RESULT: False (One or both positions are None: Ideal={ideal_pos}, Test={test_pos})")
                # --- LOGGING END ---
                return False

        # Rule 3: For all other cases (query, header, etc.), match ONLY by name.
        print("  -> Logic Path: Non-'path' parameter")
        result = ideal_name == test_name
        # --- LOGGING START ---
        print(f"  -> Comparing names: '{ideal_name}' == '{test_name}' -> {result}")
        print(f"  -> RESULT: {result}")
        # --- LOGGING END ---
        return result

    def extract_parameters_by_endpoint(self, spec: Dict, spec_name: str) -> Dict[str, Tuple[List[Tuple[str, str, str]], str]]:
        """
        Extracts parameters per endpoint, preserving order and returning the original path.
        --- MODIFIED: Uses lists instead of sets and returns (list_of_params, original_path_string) ---
        """
        params_by_endpoint = {}

        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            return {}

        for path, methods in paths.items(): # 'path' is the original path string
            if not isinstance(methods, dict):
                continue

            path_params_from_path_level = [] # Use list to preserve order
            for p in methods.get("parameters", []):
                try:
                    p_res = self.resolve_ref(p.get("$ref"), spec) if "$ref" in p else p
                    if not isinstance(p_res, dict): continue
                    schema_obj = p_res.get("schema") or {}
                    s_res = self.resolve_ref(schema_obj.get("$ref"), spec) if isinstance(schema_obj, dict) and "$ref" in schema_obj else schema_obj
                    if not isinstance(s_res, dict): s_res = {}

                    path_params_from_path_level.append((
                        p_res.get("name"),
                        p_res.get("in"),
                        s_res.get("type", "unknown")
                    ))
                except Exception as e:
                    logger.error(f"Error processing path-level parameter in '{spec_name}' at path '{path}'. Param: {p}, Error: {e}")

            for method, details in methods.items():
                if not isinstance(details, dict):
                    continue

                endpoint_key = f"{path}::{method.upper()}"
                endpoint_params_list = list(path_params_from_path_level) # Start with path-level params

                for p in details.get("parameters", []):
                    try:
                        p_res = self.resolve_ref(p.get("$ref"), spec) if "$ref" in p else p
                        if not isinstance(p_res, dict): continue
                        schema_obj = p_res.get("schema") or {}
                        s_res = self.resolve_ref(schema_obj.get("$ref"), spec) if isinstance(schema_obj, dict) and "$ref" in schema_obj else schema_obj
                        if not isinstance(s_res, dict): s_res = {}

                        endpoint_params_list.append((
                            p_res.get("name"),
                            p_res.get("in"),
                            s_res.get("type", "unknown")
                        ))
                    except Exception as e:
                        logger.error(f"Error processing an operation parameter in '{spec_name}' at endpoint '{endpoint_key}'. Param: {p}, Error: {e}")

                if "requestBody" in details:
                    rb = details.get("requestBody")
                    if isinstance(rb, dict):
                        rb_res = self.resolve_ref(rb.get("$ref"), spec) if "$ref" in rb else rb
                        if isinstance(rb_res, dict) and "content" in rb_res:
                            for content_type in ["application/json", "application/xml", "application/x-www-form-urlencoded", "multipart/form-data"]:
                                content_details = rb_res["content"].get(content_type)
                                if isinstance(content_details, dict):
                                    schema = content_details.get("schema", {})
                                    if schema:
                                        body_params = self.extract_parameters_from_schema(schema, "body", spec)
                                        endpoint_params_list.extend(body_params)
                                    break
                
                # De-duplicate while preserving order
                final_params = list(dict.fromkeys(endpoint_params_list))
                
                # Store the final ordered list and the original path string
                params_by_endpoint[endpoint_key] = (final_params, path)

        return params_by_endpoint

    def compare_parameters(self):
        """
        Compares parameters.
        --- MODIFIED: Correctly assigns positional index to path parameters. ---
        """
        ideal_data_map = self.extract_parameters_by_endpoint(self.ideal_spec, "Ideal")
        test_data_map = self.extract_parameters_by_endpoint(self.test_spec, "Test")

        def normalize_path_for_matching(path):
            normalized = path.rstrip('/') if len(path) > 1 else path
            return re.sub(r'\{[^}]+\}', '{}', normalized)

        ideal_norm_map = {}
        for key, (_, original_path) in ideal_data_map.items():
            norm_key = normalize_path_for_matching(original_path)
            ideal_norm_map.setdefault(norm_key, []).append(key)

        test_norm_map = {}
        for key, (_, original_path) in test_data_map.items():
            norm_key = normalize_path_for_matching(original_path)
            test_norm_map.setdefault(norm_key, []).append(key)
        
        all_normalized_paths = set(ideal_norm_map.keys()) | set(test_norm_map.keys())
        report = []
        processed_test_keys = set()

        for norm_path in sorted(list(all_normalized_paths)):
            ideal_endpoint_keys = ideal_norm_map.get(norm_path, [])
            test_endpoint_keys = test_norm_map.get(norm_path, [])
            
            ideal_keys_by_method = {k.split('::')[1]: k for k in ideal_endpoint_keys}
            test_keys_by_method = {k.split('::')[1]: k for k in test_endpoint_keys}
            all_methods = set(ideal_keys_by_method.keys()) | set(test_keys_by_method.keys())

            for method in all_methods:
                ideal_key = ideal_keys_by_method.get(method)
                test_key = test_keys_by_method.get(method)
                
                report_endpoint_key = ideal_key or test_key

                if ideal_key and test_key:
                    processed_test_keys.add(test_key)
                    ideal_params_raw, _ = ideal_data_map[ideal_key]
                    test_params_raw, _ = test_data_map[test_key]
                    
                    # --- THIS IS THE KEY FIX ---
                    # Helper function to assign positions based on declaration order
                    def assign_positions(params_raw_list):
                        final_params_with_pos = []
                        path_param_counter = 0
                        for p_name, p_loc, p_type in params_raw_list:
                            if p_loc == 'path':
                                # Assign position based on order of appearance
                                final_params_with_pos.append((p_name, p_loc, p_type, path_param_counter))
                                path_param_counter += 1
                            else:
                                # Other params have no position
                                final_params_with_pos.append((p_name, p_loc, p_type, None))
                        return final_params_with_pos
                    
                    ideal_params = assign_positions(ideal_params_raw)
                    test_params = assign_positions(test_params_raw)
                    # --- END OF FIX ---

                    matching_pairs, missing, extra = [], [], list(test_params)
                    for i_p in ideal_params:
                        match = next((t_p for t_p in extra if self._are_params_equivalent(i_p, t_p)), None)
                        if match: matching_pairs.append(match); extra.remove(match)
                        else: missing.append(i_p)
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": matching_pairs, "missing_list": missing, "extra_list": extra })

                elif ideal_key:
                    ideal_params_raw, _ = ideal_data_map[ideal_key]
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": [], "missing_list": ideal_params_raw, "extra_list": [] })
                
                elif test_key and test_key not in processed_test_keys:
                    test_params_raw, _ = test_data_map[test_key]
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": [], "missing_list": [], "extra_list": test_params_raw })

        self.report_data["Endpoint Parameters"] = report

    


    def extract_constraints_from_schema(self, schema: Dict, location: str, spec: Dict, 
                                      parent_key: str = "", required_fields: Set[str] = None) -> List[Tuple[str, str, str, Any]]:
        """Recursively extract constraints from a schema definition."""
        if not isinstance(schema, dict):
            return []
        
        constraints = []
        # Use an empty set as a default if none is provided.
        required_fields = required_fields or set()
        
        if "$ref" in schema:
            schema = self.resolve_ref(schema["$ref"], spec)
            if not schema: return []

        if parent_key:
            is_required = parent_key.split('.')[-1] in required_fields
            constraints.extend(
                self.extract_parameter_constraints(schema, parent_key, location, is_required)
            )

        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            object_required = set(schema.get("required", []))

            for param_name, param_schema in properties.items():
                if not isinstance(param_schema, dict): continue
                full_param_name = f"{parent_key}.{param_name}" if parent_key else param_name
                constraints.extend(
                    self.extract_constraints_from_schema(
                        param_schema, location, spec, full_param_name, object_required
                    )
                )

        elif schema.get("type") == "array":
            items_schema = schema.get("items", {})
            if isinstance(items_schema, dict):
                # The key for array items is special.
                array_key = f"{parent_key}[]" if parent_key else "[]"
                # For items inside an array, 'required' doesn't apply to the item itself.
                constraints.extend(
                    self.extract_constraints_from_schema(items_schema, location, spec, array_key, set())
                )

        for schema_type in ["oneOf", "anyOf", "allOf"]:
            if schema_type in schema:
                for sub_schema in schema[schema_type]:
                    constraints.extend(
                        self.extract_constraints_from_schema(sub_schema, location, spec, parent_key, required_fields)
                    )

        return constraints
    

    def extract_parameter_constraints(self, param_schema: Dict, param_name: str, location: str, is_required: bool) -> List[Tuple[str, str, str, Any]]:
        """Extract all constraints for a single parameter, now robust against None schemas."""
        # --- FIX: Check if the schema itself is a valid dictionary ---
        if not isinstance(param_schema, dict):
            return []

        constraints = []
        if is_required:
            constraints.append((param_name, location, "required", True))

        for key in ["type", "minLength", "maxLength", "minimum", "maximum", "multipleOf", "minItems", "maxItems"]:
            if key in param_schema:
                constraints.append((param_name, location, key, param_schema[key]))

        if "enum" in param_schema and isinstance(param_schema.get("enum"), list):
            for enum_item in param_schema["enum"]:
                constraints.append((param_name, location, "enum", enum_item))

        return constraints

    def extract_constraints_by_endpoint(self, spec: Dict, spec_name: str) -> Dict[str, List[Tuple[str, str, str, Any]]]:
        """Extracts constraints with robust error handling and correct requestBody processing."""
        constraints_by_endpoint = {}
        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            return {}

        for path, methods in paths.items():
            if not isinstance(methods, dict): continue

            path_level_constraints = []
            for p in methods.get("parameters", []):
                try:
                    p_res = self.resolve_ref(p.get("$ref"), spec) if "$ref" in p else p
                    if not isinstance(p_res, dict): continue
                    schema_to_pass = p_res.get("schema") or {}
                    param_name = p_res.get("name")
                    is_required = p_res.get("required", False)
                    # Use the main constraint extractor for path params too
                    path_level_constraints.extend(self.extract_parameter_constraints(schema_to_pass, param_name, p_res.get("in"), is_required))
                except Exception as e:
                    logger.error(f"Error processing path parameter constraint in '{spec_name}' at path '{path}'. Param: {p}, Error: {e}")

            for method, details in methods.items():
                if not isinstance(details, dict): continue
                endpoint_key = f"{path}::{method.upper()}"
                endpoint_constraints_list = list(path_level_constraints)

                for p in details.get("parameters", []):
                    try:
                        p_res = self.resolve_ref(p.get("$ref"), spec) if "$ref" in p else p
                        if not isinstance(p_res, dict): continue
                        schema_to_pass = p_res.get("schema") or {}
                        param_name = p_res.get("name")
                        is_required = p_res.get("required", False)
                        endpoint_constraints_list.extend(self.extract_parameter_constraints(schema_to_pass, param_name, p_res.get("in"), is_required))
                    except Exception as e:
                        logger.error(f"Error processing operation parameter constraint in '{spec_name}' at endpoint '{endpoint_key}'. Param: {p}, Error: {e}")
                
                # --- THIS IS THE CORRECTED requestBody LOGIC ---
                if "requestBody" in details:
                    rb = details.get("requestBody")
                    if isinstance(rb, dict):
                        rb_res = self.resolve_ref(rb.get("$ref"), spec) if "$ref" in rb else rb
                        if isinstance(rb_res, dict) and "content" in rb_res:
                            for content_type in ["application/json", "application/xml", "application/x-www-form-urlencoded", "multipart/form-data"]:
                                content_details = rb_res["content"].get(content_type)
                                if isinstance(content_details, dict):
                                    schema = content_details.get("schema", {})
                                    if schema:
                                        # Use the recursive function to handle all nested cases
                                        endpoint_constraints_list.extend(
                                            self.extract_constraints_from_schema(schema, "body", spec)
                                        )
                                    break
                
                # Deduplication logic (unchanged and correct)
                unique_constraints, seen = [], set()
                for constraint in endpoint_constraints_list:
                    try:
                        name, loc, c_type, value = constraint
                        hashable_value = value if not isinstance(value, (dict, list)) else json.dumps(value, sort_keys=True)
                        constraint_key = (name, loc, c_type, hashable_value)
                        if constraint_key not in seen:
                            unique_constraints.append(constraint)
                            seen.add(constraint_key)
                    except TypeError:
                        logger.error(f"UNHASHABLE CONSTRAINT SKIPPED: {str(constraint)}")
                        continue
                constraints_by_endpoint[endpoint_key] = unique_constraints
        return constraints_by_endpoint
    
    def _group_constraints_by_param(self, cl: List) -> Dict[str, Dict]:
        grouped = {}
        for name, loc, c_type, val in cl:
            key = f"{name}|{loc}"
            if key not in grouped: grouped[key] = {}
            if c_type == 'enum':
                if 'enum' not in grouped[key]:
                    grouped[key]['enum'] = []
                grouped[key]['enum'].append(val)
            else:
                # For all other constraints, we assume one value.
                grouped[key][c_type] = val
        return grouped

    def compare_constraints(self):
        """
        Compares parameter constraints using a robust, parameter-first approach.
        """
        ideal_constraints_map = self.extract_constraints_by_endpoint(self.ideal_spec, "Ideal")
        test_constraints_map = self.extract_constraints_by_endpoint(self.test_spec, "Test")
        ideal_param_data = self.extract_parameters_by_endpoint(self.ideal_spec, "Ideal")
        test_param_data = self.extract_parameters_by_endpoint(self.test_spec, "Test")

        def normalize_path_for_matching(path):
            normalized = path.rstrip('/') if len(path) > 1 else path
            return re.sub(r'\{[^}]+\}', '{}', normalized)

        ideal_norm_map = {}
        for key, (_, original_path) in ideal_param_data.items():
            norm_key = normalize_path_for_matching(original_path)
            ideal_norm_map.setdefault(norm_key, []).append(key)

        test_norm_map = {}
        for key, (_, original_path) in test_param_data.items():
            norm_key = normalize_path_for_matching(original_path)
            test_norm_map.setdefault(norm_key, []).append(key)
        
        all_normalized_paths = set(ideal_norm_map.keys()) | set(test_norm_map.keys())
        report = []
        processed_test_keys = set()

        for norm_path in sorted(list(all_normalized_paths)):
            ideal_endpoint_keys = ideal_norm_map.get(norm_path, [])
            test_endpoint_keys = test_norm_map.get(norm_path, [])
            
            ideal_keys_by_method = {k.split('::')[1]: k for k in ideal_endpoint_keys}
            test_keys_by_method = {k.split('::')[1]: k for k in test_endpoint_keys}
            all_methods = set(ideal_keys_by_method.keys()) | set(test_keys_by_method.keys())

            for method in all_methods:
                ideal_key = ideal_keys_by_method.get(method)
                test_key = test_keys_by_method.get(method)
                
                report_endpoint_key = ideal_key or test_key
                print(f"\n[DEBUG] Processing Endpoint: {report_endpoint_key}")

                if ideal_key and test_key:
                    processed_test_keys.add(test_key)
                    ideal_params_raw, ideal_original_path = ideal_param_data.get(ideal_key, ([], ''))
                    test_params_raw, test_original_path = test_param_data.get(test_key, ([], ''))
                    
                    def assign_positions(params_raw_list):
                        final_params_with_pos = []
                        path_param_counter = 0
                        for p_name, p_loc, p_type in params_raw_list:
                            if p_loc == 'path':
                                final_params_with_pos.append((p_name, p_loc, p_type, path_param_counter))
                                path_param_counter += 1
                            else:
                                final_params_with_pos.append((p_name, p_loc, p_type, None))
                        return final_params_with_pos
                    
                    ideal_params = assign_positions(ideal_params_raw)
                    test_params = assign_positions(test_params_raw)
                    
                    matching_param_pairs, missing_params, extra_params = [], [], list(test_params)
                    for ideal_p in ideal_params:
                        match = next((test_p for test_p in extra_params if self._are_params_equivalent(ideal_p, test_p)), None)
                        if match:
                            matching_param_pairs.append((ideal_p, match))
                            extra_params.remove(match)
                        else:
                            missing_params.append(ideal_p)
                    
                    ideal_grouped = self._group_constraints_by_param(ideal_constraints_map.get(ideal_key, []))
                    test_grouped = self._group_constraints_by_param(test_constraints_map.get(test_key, []))
                    print(f"  [DEBUG] Ideal Params Found: {ideal_params}")
                    print(f"  [DEBUG] Test Params Found:  {test_params}")
                    print(f"  [DEBUG] Matched Param Pairs: {matching_param_pairs}")
                    print(f"  [DEBUG] Missing Params:      {missing_params}")
                    print(f"  [DEBUG] Extra Params:        {extra_params}")
                    print(f"  [DEBUG] Ideal Constraints Grouped: {ideal_grouped}")
                    print(f"  [DEBUG] Test Constraints Grouped:  {test_grouped}")
                    endpoint_correct, endpoint_missing, endpoint_extra, endpoint_mismatched = [], [], [], []

                    # A) Compare constraints for matched parameters
                    for ideal_p_tuple, test_p_tuple in matching_param_pairs:
                        ideal_p_name, ideal_p_loc, _, _ = ideal_p_tuple
                        test_p_name, test_p_loc, _, _ = test_p_tuple
                        param_c_ideal = ideal_grouped.get(f"{ideal_p_name}|{ideal_p_loc}", {})
                        param_c_test = test_grouped.get(f"{test_p_name}|{test_p_loc}", {})
                        
                        # MODIFICATION START: New comparison logic for enums and other constraints
                        all_constraint_types = set(param_c_ideal.keys()) | set(param_c_test.keys())

                        for c_type in all_constraint_types:
                            if c_type == 'enum':
                                ideal_enums = set(param_c_ideal.get('enum', []))
                                test_enums = set(param_c_test.get('enum', []))

                                endpoint_correct.extend([f"{ideal_p_name}|enum: {item}" for item in sorted(list(ideal_enums & test_enums))])
                                endpoint_missing.extend([f"{ideal_p_name}|enum (Ideal: {item})" for item in sorted(list(ideal_enums - test_enums))])
                                endpoint_extra.extend([f"{test_p_name}|enum (Test: {item})" for item in sorted(list(test_enums - ideal_enums))])
                            else:
                                i_val = param_c_ideal.get(c_type)
                                t_val = param_c_test.get(c_type)
                                in_ideal = c_type in param_c_ideal
                                in_test = c_type in param_c_test

                                if in_ideal and in_test:
                                    if i_val == t_val:
                                        endpoint_correct.append(f"{ideal_p_name}|{c_type}: {i_val}")
                                    else:
                                        endpoint_mismatched.append(f"{ideal_p_name}|{c_type} (Ideal: {i_val}, Test: {t_val})")
                                elif in_ideal:
                                    endpoint_missing.append(f"{ideal_p_name}|{c_type}: {i_val}")
                                elif in_test:
                                    endpoint_extra.append(f"{test_p_name}|{c_type}: {t_val}")

                    # B) All constraints for missing parameters are 'missing'
                    for p_name, p_loc, _, _ in missing_params:
                        # MODIFICATION START: Handle enum lists correctly
                        for c_type, val in ideal_grouped.get(f"{p_name}|{p_loc}", {}).items():
                            if c_type == 'enum':
                                for item in sorted(val):
                                    endpoint_missing.append(f"{p_name}|enum (Ideal: {item})")
                            else:
                                endpoint_missing.append(f"{p_name}|{c_type}: {val}")
                            
                    # C) All constraints for extra parameters are 'extra'
                    for p_name, p_loc, _, _ in extra_params:
                        # MODIFICATION START: Handle enum lists correctly
                        for c_type, val in test_grouped.get(f"{p_name}|{p_loc}", {}).items():
                            if c_type == 'enum':
                                for item in sorted(val):
                                    endpoint_extra.append(f"{p_name}|enum (Test: {item})")
                            else:
                                endpoint_extra.append(f"{p_name}|{c_type}: {val}")
                    
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": endpoint_correct, "missing_list": endpoint_missing, "extra_list": endpoint_extra, "mismatched_list": endpoint_mismatched })

                elif ideal_key:
                    constraints = [f"{p[0]}|{p[2]}: {p[3]}" for p in ideal_constraints_map.get(ideal_key, [])]
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": [], "missing_list": constraints, "extra_list": [], "mismatched_list": []})
                
                elif test_key and test_key not in processed_test_keys:
                    constraints = [f"{p[0]}|{p[2]}: {p[3]}" for p in test_constraints_map.get(test_key, [])]
                    report.append({ "endpoint": report_endpoint_key, "correctly_identified_list": [], "missing_list": [], "extra_list": constraints, "mismatched_list": []})

        self.report_data["Parameter Constraints"] = report
        
    def extract_response_properties_from_schema(self, schema: Dict, spec: Dict, parent_key: str = "", visited_refs: set = None) -> List[Tuple[str, str]]:
        """
        Recursively extract response properties, with robust handling for nested
        and circular $refs to prevent infinite recursion.
        """
        if visited_refs is None:
            visited_refs = set()

        if not isinstance(schema, dict):
            return []
        
        properties = []
        
        if "$ref" in schema:
            ref_path = schema["$ref"]
            if ref_path in visited_refs:
                return [] # Circular reference detected, stop this path.
            
            visited_refs.add(ref_path)
            resolved_schema = self.resolve_ref(ref_path, spec)
            
            # Recurse with the resolved schema.
            # This is the key: we replace the current schema with the resolved one and continue.
            # We don't add a property for the ref itself, only for its contents.
            result = self.extract_response_properties_from_schema(resolved_schema, spec, parent_key, visited_refs)
            
            visited_refs.remove(ref_path) # Backtrack
            return result
        
        schema_type = schema.get("type")

        if schema_type == "object":
            for prop_name, prop_schema in schema.get("properties", {}).items():
                full_prop_name = f"{parent_key}.{prop_name}" if parent_key else prop_name
                # The recursive call will handle everything for this property.
                properties.extend(
                    self.extract_response_properties_from_schema(prop_schema, spec, full_prop_name, visited_refs)
                )
        
        elif schema_type == "array":
            items_schema = schema.get("items", {})
            array_key = f"{parent_key}[]" if parent_key else "[]"
            properties.extend(
                self.extract_response_properties_from_schema(items_schema, spec, array_key, visited_refs)
            )
        
        # This is the "leaf node" case. If a property is not an object or array,
        # and has a name, it's a final property we should record.
        elif parent_key:
             properties.append((parent_key, schema_type))

        for combiner in ["oneOf", "anyOf", "allOf"]:
            if combiner in schema:
                for sub_schema in schema[combiner]:
                    properties.extend(
                        self.extract_response_properties_from_schema(sub_schema, spec, parent_key, visited_refs)
                    )
                    
        return properties
    
    def extract_responses_by_endpoint(self, spec: Dict) -> Dict:
        """
        Extracts 2xx response properties with robust error handling for broken refs
        and malformed response objects (e.g., '200': null).
        """
        responses_map = {}

        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            logger.error(f"Spec is missing a valid 'paths' dictionary. Cannot extract responses.")
            return {}

        for path, methods in paths.items():
            if not isinstance(methods, dict):
                logger.warning(f"Skipping malformed path item: Path '{path}' is not a valid dictionary.")
                continue

            for method, details in methods.items():
                if not isinstance(details, dict):
                    logger.warning(f"Skipping malformed operation object for endpoint '{method.upper()} {path}'.")
                    continue
                
                endpoint_key = f"{path}::{method.upper()}"
                responses_map[endpoint_key] = {}

                responses_obj = details.get("responses")
                if not isinstance(responses_obj, dict):
                    continue

                for code, resp in responses_obj.items():
                    # --- THIS IS THE CRUCIAL FIX ---
                    # Check if the response object itself is a valid dictionary before processing.
                    if not isinstance(resp, dict):
                        logger.warning(f"Skipping malformed response for endpoint '{endpoint_key}' status code '{code}'. Expected a dictionary, found None or other type.")
                        continue
                    # --- END OF FIX ---

                    is_2xx = False
                    try:
                        is_2xx = 200 <= int(code) < 300
                    except (ValueError, TypeError):
                        pass

                    if not is_2xx and str(code).upper() not in ["2XX", "DEFAULT"]:
                        continue
                    
                    resp_res = self.resolve_ref(resp.get("$ref"), spec) if "$ref" in resp else resp
                    if not isinstance(resp_res, dict):
                        logger.warning(f"Response for {endpoint_key} status {code} is not a valid dictionary after resolution. Skipping.")
                        continue
                    
                    response_payload = {'type': 'none', 'properties': []}
                    content = resp_res.get("content")
                    if isinstance(content, dict):
                        for content_type in ["application/json", "application/xml"]:
                            content_details = content.get(content_type)
                            if isinstance(content_details, dict):
                                schema = content_details.get("schema", {})
                                if "$ref" in schema:
                                    schema = self.resolve_ref(schema.get("$ref"), spec)

                                if schema and isinstance(schema, dict):
                                    response_payload['type'] = schema.get("type", "unknown")
                                    response_payload['properties'] = self.extract_response_properties_from_schema(schema, spec)
                                break
                            
                    responses_map[endpoint_key][code] = response_payload

        return responses_map



    # --- REVISED COMPARISON METHOD ---
    def compare_responses(self):
        """
        Compares responses, first checking for structural (type) mismatches,
        then comparing properties if the structure is the same.
        """
        ideal_map, test_map = self.extract_responses_by_endpoint(self.ideal_spec), self.extract_responses_by_endpoint(self.test_spec)
        all_endpoints = sorted(list(set(ideal_map.keys()) | set(test_map.keys())))

        status_code_report, properties_report = [], []
        for endpoint in all_endpoints:
            ideal_endpoint_res = ideal_map.get(endpoint, {})
            test_endpoint_res = test_map.get(endpoint, {})
            
            ideal_codes = set(ideal_endpoint_res.keys())
            test_codes = set(test_endpoint_res.keys())
            status_code_report.append({ "endpoint": endpoint, "correctly_identified_list": list(ideal_codes & test_codes), "missing_list": list(ideal_codes - test_codes), "extra_list": list(test_codes - ideal_codes) })
            
            # --- Per-Endpoint Property Analysis ---
            correct, missing, mismatched, extra = [], [], [], []
            mismatch_reason = ""

            # We only compare properties for status codes present in both
            matching_codes = ideal_codes & test_codes
            if matching_codes:
                # For simplicity, we'll compare based on the first matching code found.
                # A more complex script could do this per-code.
                first_code = sorted(list(matching_codes))[0]
                ideal_payload = ideal_endpoint_res[first_code]
                test_payload = test_endpoint_res[first_code]
                
                ideal_type, ideal_props_list = ideal_payload['type'], ideal_payload['properties']
                test_type, test_props_list = test_payload['type'], test_payload['properties']

                if ideal_type != test_type:
                    # --- HANDLE STRUCTURAL MISMATCH ---
                    mismatch_reason = f"Structural Mismatch: Ideal is '{ideal_type}', Test is '{test_type}'"
                    missing = [f"{p[0]}:{p[1]}" for p in ideal_props_list]
                    extra = [f"{p[0]}:{p[1]}" for p in test_props_list]
                else:
                    # --- SAME STRUCTURE, PROCEED WITH PROPERTY COMPARISON ---
                    ideal_props_dict = dict(ideal_props_list)
                    test_props_dict = dict(test_props_list)
                    
                    for name, i_type in ideal_props_dict.items():
                        if name in test_props_dict:
                            if test_props_dict[name] == i_type: correct.append(f"{name}:{i_type}")
                            else: mismatched.append(f"{name} (Ideal: {i_type}, Test: {test_props_dict[name]})")
                        else: missing.append(f"{name}:{i_type}")
                    extra = [f"{n}:{t}" for n, t in test_props_dict.items() if n not in ideal_props_dict]
            
            # Aggregate properties from codes that were entirely missing or extra
            missing_codes_props = [p for code in (ideal_codes - test_codes) for p in ideal_endpoint_res[code]['properties']]
            missing.extend([f"{p[0]}:{p[1]}" for p in missing_codes_props])
            extra_codes_props = [p for code in (test_codes - ideal_codes) for p in test_endpoint_res[code]['properties']]
            extra.extend([f"{p[0]}:{p[1]}" for p in extra_codes_props])
            
            properties_report.append({ 
                "endpoint": endpoint, 
                "structural_mismatch_reason": mismatch_reason, # Add the new reason field
                "correctly_identified_list": correct, 
                "missing_list": missing, 
                "extra_list": extra, 
                "mismatched_list": mismatched 
            })
        
        self.report_data["Response Status Codes"] = status_code_report
        self.report_data["Response Properties"] = properties_report


    def run_all_comparisons(self):
        """Run all comparison methods to populate report_data."""
        logger.info("Comparing endpoint methods..."); self.compare_endpoint_methods()
        logger.info("Comparing parameters..."); self.compare_parameters()
        logger.info("Comparing constraints..."); self.compare_constraints()
        logger.info("Comparing responses..."); self.compare_responses()
        logger.info("All comparisons complete.")


def generate_excel_reports(report_data: Dict, output_filename: str):
    """
    Generates a single, sorted, and color-coded Excel file with multiple sheets
    from the report data.
    """
    logger.info(f"Generating single Excel report with multiple sheets: {output_filename}")
    
    with pd.ExcelWriter(output_filename, engine='openpyxl') as writer:
        for metric_name, data in report_data.items():
            if not data:
                logger.warning(f"No data to generate report for {metric_name}. Skipping sheet.")
                continue

            df = pd.DataFrame(data)

            # --- 1. Calculate Counts & Format Lists ---
            count_cols = []
            for col in ["correctly_identified", "missing", "extra", "mismatched"]:
                list_col = f"{col}_list"
                if list_col in df.columns:
                    count_col = f"{col}_count"
                    count_cols.append(count_col)
                    df[count_col] = df[list_col].apply(len)
                    df[list_col] = df[list_col].apply(lambda x: "\n".join(sorted(map(str, x))) if isinstance(x, list) else "")

            # --- 2. Calculate Totals and Raw Precision/Recall ---
            df["total_correct"] = df.get("correctly_identified_count", 0) + df.get("missing_count", 0) + df.get("mismatched_count", 0)
            df["total_identified"] = df.get("correctly_identified_count", 0) + df.get("extra_count", 0) + df.get("mismatched_count", 0)
            
            df["precision_raw"] = (df["correctly_identified_count"] / df["total_identified"]).fillna(0)
            df["recall_raw"] = (df["correctly_identified_count"] / df["total_correct"]).fillna(0)
            
            # --- 3. Separate OVERALL row before sorting ---
            summary_df = pd.DataFrame()
            if not df.empty:
                summary_raw = {df.columns[0]: "OVERALL"}
                for col in count_cols: summary_raw[col] = df[col].sum()
                
                summary_raw["total_correct"] = summary_raw.get("correctly_identified_count", 0) + summary_raw.get("missing_count", 0)
                summary_raw["total_identified"] = summary_raw.get("correctly_identified_count", 0) + summary_raw.get("extra_count", 0)
                
                prec = (summary_raw["correctly_identified_count"] / summary_raw["total_identified"]) if summary_raw.get("total_identified", 0) > 0 else 0
                rec = (summary_raw["correctly_identified_count"] / summary_raw["total_correct"]) if summary_raw.get("total_correct", 0) > 0 else 0
                summary_raw["precision_raw"] = prec
                summary_raw["recall_raw"] = rec
                summary_df = pd.DataFrame([summary_raw])

            # --- 4. Sort and combine ---
            df = df.sort_values(by=['precision_raw', 'recall_raw'], ascending=[True, True])
            df = pd.concat([df, summary_df], ignore_index=True)

            # --- 5. Format percentages ---
            def format_metric(row, metric_col, total_col):
                if row[total_col] == 0: return "N/A"
                return f"{row[metric_col]:.2%}"
            df['precision'] = df.apply(lambda row: format_metric(row, 'precision_raw', 'total_identified'), axis=1)
            df['recall'] = df.apply(lambda row: format_metric(row, 'recall_raw', 'total_correct'), axis=1)
            
            # --- 6. Define Column Order ---
            first_col = df.columns[0]
            ordered_cols = [first_col, 'structural_mismatch_reason', 'correctly_identified_count', 'missing_count', 'mismatched_count', 'extra_count', 'total_identified', 'total_correct', 'precision', 'recall', 'correctly_identified_list', 'missing_list', 'mismatched_list', 'extra_list']
            df_final = df[[c for c in ordered_cols if c in df.columns]]

            # --- 7. Write to a new sheet in the single Excel file ---
            sheet_name = metric_name[:31] # Excel sheet names must be <= 31 chars
            df_final.to_excel(writer, index=False, sheet_name=sheet_name)
            worksheet = writer.sheets[sheet_name]

            # --- 8. Apply Color Coding and Formatting ---
            red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            precision_col_letter = chr(65 + df_final.columns.get_loc('precision'))
            recall_col_letter = chr(65 + df_final.columns.get_loc('recall'))
            
            for i, row in df.iterrows():
                row_num = i + 2 # Excel rows are 1-based, plus header
                if row['precision_raw'] < 0.8 and row['total_identified'] > 0: worksheet[f'{precision_col_letter}{row_num}'].fill = red_fill
                elif row['precision_raw'] >= 0.8 and row['total_identified'] > 0: worksheet[f'{precision_col_letter}{row_num}'].fill = green_fill
                if row['recall_raw'] < 0.8 and row['total_correct'] > 0: worksheet[f'{recall_col_letter}{row_num}'].fill = red_fill
                elif row['recall_raw'] >= 0.8 and row['total_correct'] > 0: worksheet[f'{recall_col_letter}{row_num}'].fill = green_fill
            
            for idx, col in enumerate(df_final):
                max_len = max((df_final[col].astype(str).map(len).max(), len(str(col)))) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 60)
            
            logger.info(f"Successfully wrote sheet '{sheet_name}' to {output_filename}")

def remap_paths_interactively(ideal_spec: Dict, test_spec: Dict) -> Tuple[Dict, Dict]:
    """
    Finds path collisions, asks the user to map them, and returns new spec
    dictionaries with the paths renamed to be identical for comparison.
    """
    print("\n--- Analyzing Paths for Potential Collisions ---")
    
    # Helper to get the normalized form of a single path
    def get_normalized_path(path: str) -> str:
        norm_path = re.sub(r"^/api/v\d+/?", "/", path)
        norm_path = re.sub(r"^/api/?", "/", norm_path)
        norm_path = re.sub(r"\{[^}]+\}", "{placeholder}", norm_path)
        if not norm_path: norm_path = "/"
        elif not norm_path.startswith("/"): norm_path = "/" + norm_path
        return norm_path

    # Step 1: Group original paths by their normalized form
    ideal_groups = {}
    for path in ideal_spec.get("paths", {}):
        norm_path = get_normalized_path(path)
        ideal_groups.setdefault(norm_path, []).append(path)

    test_groups = {}
    for path in test_spec.get("paths", {}):
        norm_path = get_normalized_path(path)
        test_groups.setdefault(norm_path, []).append(path)

    # Step 2: Create new spec dictionaries to populate
    # We will rename paths in these copies
    remapped_ideal_spec = copy.deepcopy(ideal_spec)
    remapped_test_spec = copy.deepcopy(test_spec)
    remapped_ideal_spec['paths'] = {}
    remapped_test_spec['paths'] = {}
    
    processed_ideal_paths = set()
    processed_test_paths = set()
    
    # Step 3: Iterate through groups and handle mappings
    all_norm_paths = sorted(list(set(ideal_groups.keys()) | set(test_groups.keys())))
    
    collision_counter = 0
    for norm_path in all_norm_paths:
        ideal_hits = ideal_groups.get(norm_path, [])
        test_hits = test_groups.get(norm_path, [])
        
        # This is where we need manual intervention
        if len(ideal_hits) > 1 or len(test_hits) > 1:
            collision_counter += 1
            print(f"\n--- Manual Mapping Required for Colliding Path Group: '{norm_path}' ---")
            print("Ideal Spec Paths:")
            for i, p in enumerate(ideal_hits): print(f"  {i+1}: {p}")
            
            print("\nTest Spec Paths:")
            for i, p in enumerate(test_hits): print(f"  {i+1}: {p}")
            
            unmapped_test_indices = set(range(len(test_hits)))
            unmatched_ideal = list(ideal_hits)
            unmatched_test = list(test_hits)
            
            for ideal_p in list(unmatched_ideal): # Iterate over a copy
                if ideal_p in unmatched_test:
                    # Perfect match found! Handle it and remove from the "unmatched" lists.
                    remapped_ideal_spec['paths'][ideal_p] = ideal_spec['paths'][ideal_p]
                    remapped_test_spec['paths'][ideal_p] = test_spec['paths'][ideal_p]
                    
                    processed_ideal_paths.add(ideal_p)
                    processed_test_paths.add(ideal_p)
                    
                    unmatched_ideal.remove(ideal_p)
                    unmatched_test.remove(ideal_p)
            
            # Second, only if there are still unmatched paths, fall back to manual mapping
            if unmatched_ideal or unmatched_test:
                collision_counter += 1
                print(f"\n--- Manual Mapping Required for Ambiguous Path Group: '{norm_path}' ---")
                print("Ideal Spec Paths (unmatched):")
                for i, p in enumerate(unmatched_ideal): print(f"  {i+1}: {p}")
                
                print("\nTest Spec Paths (unmatched):")
                for i, p in enumerate(unmatched_test): print(f"  {i+1}: {p}")
                
                unmapped_test_indices = set(range(len(unmatched_test)))
                
                for i, ideal_p in enumerate(unmatched_ideal):
                    while True:
                        if not unmapped_test_indices:
                             print(f"No remaining test paths to map for Ideal Path {i+1} ('{ideal_p}'). Marking as missing.")
                             user_input = 's'
                        else:
                            prompt = f"\nMatch for Ideal Path {i+1} ('{ideal_p}')?\nEnter a number from the Test list, or 's' to mark as missing (skip): "
                            user_input = input(prompt).strip().lower()

                        if user_input == 's':
                            unique_path_key = f"{norm_path}::missing::{ideal_p}"
                            remapped_ideal_spec['paths'][unique_path_key] = ideal_spec['paths'][ideal_p]
                            break
                        try:
                            test_idx = int(user_input) - 1
                            if test_idx in unmapped_test_indices:
                                test_p = unmatched_test[test_idx]
                                
                                unique_path_key = f"{norm_path}::pair_{i+1}"
                                remapped_ideal_spec['paths'][unique_path_key] = ideal_spec['paths'][ideal_p]
                                remapped_test_spec['paths'][unique_path_key] = test_spec['paths'][test_p]

                                unmapped_test_indices.remove(test_idx)
                                processed_test_paths.add(test_p)
                                break
                            else:
                                print("Error: Invalid or already mapped number.")
                        except (ValueError, IndexError):
                            print("Error: Invalid input.")
                    
                    processed_ideal_paths.add(ideal_p)

                # Any remaining unmapped test paths are "extra"
                for test_idx in unmapped_test_indices:
                    test_p = unmatched_test[test_idx]
                    unique_path_key = f"{norm_path}::extra::{test_p}"
                    remapped_test_spec['paths'][unique_path_key] = test_spec['paths'][test_p]
                    processed_test_paths.add(test_p)
                
    if collision_counter == 0:
        print("No path collisions detected. Proceeding automatically.")
        
    # Step 4: Add all non-colliding paths back to the remapped specs
    for path, data in ideal_spec.get('paths', {}).items():
        if path not in processed_ideal_paths:
            remapped_ideal_spec['paths'][path] = data
            
    for path, data in test_spec.get('paths', {}).items():
        if path not in processed_test_paths:
            remapped_test_spec['paths'][path] = data
            
    print("\n--- Path Remapping Complete ---")
    return remapped_ideal_spec, remapped_test_spec

def calculate_summary_metrics(report_data: Dict) -> Dict:
    """Calculates overall precision and recall for key metrics."""
    summary = {}
    metrics_to_summarize = [
        "Endpoint Methods", 
        "Endpoint Parameters", # This is the request parameters
        "Parameter Constraints" # This is the request constraints
    ]
    
    for metric_name in metrics_to_summarize:
        if metric_name not in report_data or not report_data[metric_name]:
            summary[metric_name] = {"precision": 0, "recall": 0}
            continue
            
        df = pd.DataFrame(report_data[metric_name])
        correct_count = df[f"correctly_identified_list"].apply(len).sum()
        missing_count = df[f"missing_list"].apply(len).sum()
        extra_count = df[f"extra_list"].apply(len).sum()
        # Mismatched counts for constraints
        mismatched_count = df.get("mismatched_list", pd.Series(dtype='object')).apply(len).sum() if "mismatched_list" in df.columns else 0

        total_correct_in_ideal = correct_count + missing_count + mismatched_count
        total_identified_in_test = correct_count + extra_count + mismatched_count
        
        precision = (correct_count / total_identified_in_test) if total_identified_in_test > 0 else 1.0
        recall = (correct_count / total_correct_in_ideal) if total_correct_in_ideal > 0 else 1.0
        
        summary[metric_name] = {"precision": precision, "recall": recall}
        
    return summary

def run_and_get_summary(ideal_spec_path, test_spec_path, output_filename):
    """Runs the entire 'other' evaluation and returns summary metrics."""
    try:
        print("Loading specifications for 'other' evaluation...")
        ideal_spec = yaml.safe_load(open(ideal_spec_path, 'r', encoding='utf-8'))
        test_spec = yaml.safe_load(open(test_spec_path, 'r', encoding='utf-8'))
        
        # The interactive part will be handled when the master script calls this
        remapped_ideal, remapped_test = remap_paths_interactively(ideal_spec, test_spec)
        
        print("Preprocessing specifications...")
        processed_ideal = preprocess_spec(remapped_ideal)
        processed_test = preprocess_spec(remapped_test)
        
        evaluator = OpenAPIEvaluator(processed_ideal, processed_test)
        
        print("Running all 'other' comparisons...")
        evaluator.run_all_comparisons()
        
        print(f"Generating single Excel report '{output_filename}'...")
        generate_excel_reports(evaluator.report_data, output_filename)
        
        # Calculate and return the summary
        summary_metrics = calculate_summary_metrics(evaluator.report_data)
        return summary_metrics

    except Exception as e:
        logger.error(f"A critical error occurred during 'other' evaluation: {e}", exc_info=True)
        print(f"\nAn error occurred. See openapi_comparison.log for details. Error: {e}")
        return None

def main():
    """Main function to run the evaluation."""
    if len(sys.argv) != 3:
        print("Usage: python other_evaluator.py <ideal_spec.json|yaml> <test_spec.json|yaml>")
        sys.exit(1)
    
    ideal_spec_path, test_spec_path = sys.argv[1], sys.argv[2]
    
    # Call the main logic function
    run_and_get_summary(
        ideal_spec_path,
        test_spec_path,
        "other_evaluation_results.xlsx" # Default output name when run standalone
    )

if __name__ == "__main__":
    main()