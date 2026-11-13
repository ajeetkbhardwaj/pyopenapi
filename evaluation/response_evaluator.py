#!/usr/bin/env python3
"""
OpenAPI Response Evaluation Script

This script compares the response definitions of two OpenAPI specifications
(an ideal spec and a test spec) and generates detailed evaluation results.

It evaluates two aspects separately:
1.  Response Parameters: The presence and structure of fields in the response body.
2.  Response Constraints: The validation rules applied to those fields.

The script runs evaluations for two scoring modes simultaneously and presents
a summary of both. The detailed report sheets default to 'flat' mode but can
be switched to 'decay' mode via a command-line argument.
"""
import json
import yaml
import logging
import copy
import re
import argparse
import pandas as pd
from openpyxl.styles import PatternFill
from openpyxl.formatting.rule import Rule
from openpyxl.styles.differential import DifferentialStyle
from typing import Dict, List, Tuple, Any

# --- Boilerplate Setup (Logging and Helper Functions) ---

# Configure logging
LOG_FILENAME = "response_evaluation.log"
logging.basicConfig(
    filename=LOG_FILENAME,
    level=logging.DEBUG,  # Set to logging.DEBUG for verbose output
    format="%(asctime)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s",
    filemode='w'
)
logger = logging.getLogger(__name__)

def remove_descriptions(data: Any):
    """Recursively remove descriptions, summaries, examples, and tags."""
    if isinstance(data, list):
        for item in data:
            remove_descriptions(item)
    elif isinstance(data, dict):
        for key in ["description", "summary", "example", "tags"]:
            if key in data:
                del data[key]
        for value in data.values():
            remove_descriptions(value)

def normalize_paths(data: Dict):
    """Normalize API paths to be generic."""
    if "paths" in data:
        updated_paths = {}
        for path, methods in data["paths"].items():
            normalized_path = re.sub(r"\{[^}]+\}", "{placeholder}", path)
            if normalized_path in updated_paths:
                logger.warning(f"Normalized path clash for '{path}'. Overwriting.")
            updated_paths[normalized_path] = methods
        data["paths"] = updated_paths

def preprocess_spec(spec: Dict) -> Dict:
    """Apply all preprocessing steps to a spec."""
    processed_spec = copy.deepcopy(spec)
    remove_descriptions(processed_spec)
    normalize_paths(processed_spec)
    return processed_spec

def load_spec_from_file(file_path: str) -> Dict:
    """Load OpenAPI specification from JSON or YAML file."""
    logger.info(f"Loading spec from file: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            if file_path.lower().endswith(('.yaml', '.yml')):
                return yaml.safe_load(f)
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading or parsing spec from {file_path}: {e}", exc_info=True)
        raise

# --- Core Response Evaluation Logic ---

class ResponseEvaluator:
    """
    Compares and evaluates the response definitions of two OpenAPI specifications.
    """
    CONSTRAINT_KEYS = {
        "type", "default",
        "minLength", "maxLength", "pattern",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minItems", "maxItems",
        "enum",
    }

    def __init__(self, ideal_spec: Dict, test_spec: Dict):
        self.ideal_spec = ideal_spec
        self.test_spec = test_spec
        self.ideal_components = ideal_spec.get("components", {})
        self.test_components = test_spec.get("components", {})
        logger.info("ResponseEvaluator initialized.")

    def _resolve_ref(self, ref: str, spec_type: str) -> Dict:
        """Resolves a $ref string to its component definition."""
        if not ref.startswith("#/components/"):
            logger.warning(f"Cannot resolve non-local or non-component ref: {ref}")
            return {}
        
        parts = ref.split('/')
        if len(parts) != 4:
            logger.warning(f"Malformed component ref: {ref}")
            return {}
            
        _, _, component_type, component_name = parts
        components = self.ideal_components if spec_type == 'ideal' else self.test_components
        resolved = components.get(component_type, {}).get(component_name, {})
        if not resolved:
            logger.warning(f"Failed to resolve ref '{ref}' in {spec_type} spec.")
        return copy.deepcopy(resolved)

    
    def _flatten_allof(self, schema: Dict, spec_type: str) -> Dict:
        """
        Recursively flattens 'allOf' keywords in a schema into a single schema,
        correctly merging properties and required fields.
        """
        # 1. First, resolve any top-level $ref
        if '$ref' in schema:
            schema = self._resolve_ref(schema['$ref'], spec_type)
            
        # 2. If there's no 'allOf', return the schema as is.
        if 'allOf' not in schema:
            return schema

        # 3. Start the flattened schema with all keys from the original schema
        #    EXCEPT for 'allOf' itself. This correctly includes properties
        #    defined at the same level.
        flattened_schema = {k: v for k, v in schema.items() if k != 'allOf'}
        
        # 4. Now, iterate through the schemas within 'allOf' and merge them in.
        for sub_schema_def in schema.get('allOf', []):
            # Recursively flatten the sub-schema first
            sub_schema = self._flatten_allof(sub_schema_def, spec_type)
            
            # Merge its properties and required fields
            for key, value in sub_schema.items():
                if key == 'properties':
                    flattened_schema.setdefault('properties', {}).update(value)
                elif key == 'required':
                    # Ensure 'required' list exists and add new items without duplicates
                    existing_required = flattened_schema.setdefault('required', [])
                    for item in value:
                        if item not in existing_required:
                            existing_required.append(item)
                else:
                    # For other keys, the last one in the 'allOf' list wins
                    flattened_schema[key] = value
        
        # Optional: sort the required list for consistency
        if 'required' in flattened_schema:
            flattened_schema['required'].sort()
            
        return flattened_schema

    def _get_constraints_for_property(self, prop_schema: Dict, prop_name: str, depth: int) -> List:
        """Extracts constraints for a single property schema."""
        found_constraints = []
        for key in self.CONSTRAINT_KEYS:
            if key in prop_schema:
                value = prop_schema[key]
                if isinstance(value, list):
                    try: value = tuple(sorted(value))
                    except TypeError: value = tuple(value)
                constraint_tuple = (prop_name, key, value, depth)
                logger.debug(f"{'  ' * depth}    -> Found Constraint: {constraint_tuple}")
                found_constraints.append(constraint_tuple)
        return found_constraints

    def _extract_from_schema(self, schema: Dict, spec_type: str) -> Tuple[List, List]:
        """Public-facing entry point for schema extraction."""
        if not isinstance(schema, dict) or not schema:
            return [], []
        
        flattened_schema = self._flatten_allof(schema, spec_type)
        params, constraints = [], []
        self._recursive_extractor(flattened_schema, spec_type, "", 0, params, constraints, set())
        return params, constraints

    def _recursive_extractor(self, schema: Dict, spec_type: str, parent_key: str, depth: int, params: List, constraints: List, visited_refs: set):
        """The recursive helper that correctly processes flattened schemas."""
       
        if schema and '$ref' in schema:
            ref_path = schema['$ref']
            if ref_path in visited_refs:
                logger.warning(f"Circular reference detected at '{parent_key}' pointing to '{ref_path}'. Halting recursion for this branch.")
                # Add a placeholder parameter to indicate a circular ref was found
                param_tuple = (parent_key, 'circular_ref', depth)
                params.append(param_tuple)
                return # Stop recursing
            visited_refs.add(ref_path)

        if not isinstance(schema, dict):
            logger.error(f"Schema for '{parent_key}' is not a dictionary (found type: {type(schema)}). Halting this branch.")
            # We can add a placeholder parameter to the results to make it clear
            # that a malformed schema was encountered and skipped.
            param_tuple = (parent_key, 'malformed_schema', depth)
            params.append(param_tuple)
            return
        indent = "  " * depth
        logger.debug(f"{indent}--> Processing key='{parent_key}', depth={depth}")
        schema = self._flatten_allof(schema, spec_type)
        schema_type = schema.get("type")

        param_name = parent_key if parent_key else "<root>"
        param_tuple = (param_name, schema_type or 'unknown', depth)
        params.append(param_tuple)
        constraints.extend(self._get_constraints_for_property(schema, param_name, depth))
        
        if schema_type == 'object':
            required_children = set(schema.get("required", []))
            for required_name in required_children:
                full_child_name = f"{param_name}.{required_name}"
                constraint_tuple = (full_child_name, 'required', True, depth + 1)
                logger.debug(f"{indent}    -> Found Required Constraint: {constraint_tuple}")
                constraints.append(constraint_tuple)

            for prop_name, prop_schema in schema.get("properties", {}).items():
                if prop_schema is None:
                    logger.warning(f"Skipping property '{prop_name}' in '{parent_key}' because its schema is null.")
                    continue
                full_prop_name = f"{param_name}.{prop_name}"
                self._recursive_extractor(prop_schema, spec_type, full_prop_name, depth + 1, params, constraints, visited_refs.copy())
        elif schema_type == 'array':
            if 'items' in schema:
                item_name = f"{param_name}[]"
                self._recursive_extractor(schema['items'], spec_type, item_name, depth + 1, params, constraints, visited_refs.copy())
        
        logger.debug(f"{indent}<-- Finished key='{param_name}'")

    def _extract_responses_by_endpoint(self, spec: Dict, spec_type: str) -> Dict:
        """Extracts all response parameters and constraints, organized by endpoint."""
        endpoint_data = {}
        for path, methods in spec.get("paths", {}).items():
            for method, details in methods.items():
                if method.lower() not in ["get", "post", "put", "delete", "patch", "head", "options"]:
                    continue
                
                if not details:
                    logger.warning(f"Skipping empty operation definition for {path}::{method.upper()}")
                    continue
                
                endpoint_key = f"{path}::{method.upper()}"
                
                # --- START: DIAGNOSTIC LOGGING ---
                # Log which endpoint we are about to process. This will be the last such message before the crash.
                logger.info(f"Processing endpoint: {endpoint_key}")
                
                # Also, check if 'details' is a dictionary. If not, the .get() call will fail.
                if not isinstance(details, dict):
                    logger.error(f"FATAL: The definition for endpoint '{endpoint_key}' is not a valid object (found type: {type(details)}). Skipping.")
                    continue
                
                endpoint_data[endpoint_key] = {}
                
                responses = details.get("responses") or {}
                
                # The original failing line is below. The logs above will pinpoint the cause.
                for status_code, response_def in responses.items():
                    if not (status_code.startswith('2') or status_code.lower() == 'default'):
                        continue
                    
                    response_body = response_def
                    if "$ref" in response_body:
                        response_body = self._resolve_ref(response_body["$ref"], spec_type)
                    
                    schema = None
                    content_map = response_body.get("content", {})
                    if content_map:
                        for content_type, content_details in content_map.items():
                            if 'json' in content_type.lower():
                                schema = content_details.get("schema")
                                if schema:
                                    logger.debug(f"Found schema for endpoint {endpoint_key} under content-type '{content_type}'")
                                    break
                                
                        if not schema:
                            # Use .get() on the first item to safely handle cases where a content type
                            # is listed but has no schema (e.g., content: {'text/plain': {}})
                            first_content_type = next(iter(content_map))
                            first_content_details = content_map.get(first_content_type, {})
                            schema = first_content_details.get("schema")
                            
                            if schema:
                                logger.debug(f"No JSON schema found. Using fallback schema for {endpoint_key} from '{first_content_type}'")

                    if schema:
                        params, constraints = self._extract_from_schema(schema, spec_type)
                        endpoint_data[endpoint_key][status_code] = {
                            "params": sorted(list(set(params))),
                            "constraints": sorted(list(set(constraints)))
                        }
        return endpoint_data

    @staticmethod
    def _compare_items(ideal_items: List, test_items: List, mode: str, decay: float) -> Dict:
        """Generic comparison function for parameters or constraints."""
        def get_score(item: Tuple) -> float:
            if mode == 'flat': return 1.0
            depth = item[-1]
            return decay ** depth
        
        ideal_set = set(ideal_items)
        test_set = set(test_items)
        
        true_positives = ideal_set.intersection(test_set)
        false_positives = test_set.difference(ideal_set)
        false_negatives = ideal_set.difference(test_set)
        
        tp_score = sum(get_score(item) for item in true_positives)
        fp_score = sum(get_score(item) for item in false_positives)
        fn_score = sum(get_score(item) for item in false_negatives)
        
        precision = tp_score / (tp_score + fp_score) if (tp_score + fp_score) > 0 else 1.0
        recall = tp_score / (tp_score + fn_score) if (tp_score + fn_score) > 0 else 1.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "tp_score": tp_score, "fp_score": fp_score, "fn_score": fn_score,
            "precision": precision, "recall": recall, "f1_score": f1,
            "matches": sorted(list(true_positives)),
            "extra_items": sorted(list(false_positives)),
            "missing_items": sorted(list(false_negatives)),
        }

    @staticmethod
    def evaluate(ideal_data: Dict, test_data: Dict, mode: str = 'decay', decay_factor: float = 0.8) -> Dict:
        """Main evaluation driver. Now a static method."""
        logger.info(f"Starting response evaluation with mode='{mode}' and decay_factor={decay_factor}")
        
        all_endpoints = sorted(list(set(ideal_data.keys()) | set(test_data.keys())))
        endpoint_breakdown = {}
        total_param_tp, total_param_fp, total_param_fn = 0.0, 0.0, 0.0
        total_constraint_tp, total_constraint_fp, total_constraint_fn = 0.0, 0.0, 0.0

        for endpoint in all_endpoints:
            ideal_responses = ideal_data.get(endpoint, {})
            test_responses = test_data.get(endpoint, {})
            all_status_codes = sorted(list(set(ideal_responses.keys()) | set(test_responses.keys())))
            endpoint_breakdown[endpoint] = {}

            for code in all_status_codes:
                ideal_items = ideal_responses.get(code, {"params": [], "constraints": []})
                test_items = test_responses.get(code, {"params": [], "constraints": []})
                param_results = ResponseEvaluator._compare_items(ideal_items["params"], test_items["params"], mode, decay_factor)
                constraint_results = ResponseEvaluator._compare_items(ideal_items["constraints"], test_items["constraints"], mode, decay_factor)
                endpoint_breakdown[endpoint][code] = {"parameters": param_results, "constraints": constraint_results}
                total_param_tp += param_results["tp_score"]
                total_param_fp += param_results["fp_score"]
                total_param_fn += param_results["fn_score"]
                total_constraint_tp += constraint_results["tp_score"]
                total_constraint_fp += constraint_results["fp_score"]
                total_constraint_fn += constraint_results["fn_score"]
        
        param_prec = total_param_tp / (total_param_tp + total_param_fp) if (total_param_tp + total_param_fp) > 0 else 1.0
        param_rec = total_param_tp / (total_param_tp + total_param_fn) if (total_param_tp + total_param_fn) > 0 else 1.0
        param_f1 = 2 * (param_prec * param_rec) / (param_prec + param_rec) if (param_prec + param_rec) > 0 else 0.0
        const_prec = total_constraint_tp / (total_constraint_tp + total_constraint_fp) if (total_constraint_tp + total_constraint_fp) > 0 else 1.0
        const_rec = total_constraint_tp / (total_constraint_tp + total_constraint_fn) if (total_constraint_tp + total_constraint_fn) > 0 else 1.0
        const_f1 = 2 * (const_prec * const_rec) / (const_prec + const_rec) if (const_prec + const_rec) > 0 else 0.0

        summary = {
            "parameters_summary": {
                "precision": param_prec, "recall": param_rec, "f1_score": param_f1,
                "total_tp_score": total_param_tp, "total_fp_score": total_param_fp, "total_fn_score": total_param_fn
            },
            "constraints_summary": {
                "precision": const_prec, "recall": const_rec, "f1_score": const_f1,
                "total_tp_score": total_constraint_tp, "total_fp_score": total_constraint_fp, "total_fn_score": total_constraint_fn
            }
        }
        return {"summary": summary, "endpoint_breakdown": endpoint_breakdown}

# --- Reporting ---

def auto_format_sheet(writer, df, sheet_name):
    """
    Applies auto-formatting to a worksheet: adjusts column widths and colors rows/cells.
    """
    worksheet = writer.sheets[sheet_name]

    # 1. Auto-adjust column widths
    for i, column in enumerate(df.columns):
        max_len = df[column].astype(str).map(len).max()
        column_len = max(max_len, len(column))
        worksheet.column_dimensions[chr(65 + i)].width = column_len + 2

    # 2. Define fills
    green_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    orange_fill = PatternFill(start_color='FFD966', end_color='FFD966', fill_type='solid')
    red_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')

    # 3. Apply formatting based on sheet name
    if sheet_name == "Summary":
        green_dxf = DifferentialStyle(fill=green_fill)
        orange_dxf = DifferentialStyle(fill=orange_fill)
        red_dxf = DifferentialStyle(fill=red_fill)
        green_rule = Rule(type='cellIs', operator='greaterThan', formula=[0.9], dxf=green_dxf)
        orange_rule = Rule(type='cellIs', operator='between', formula=[0.7, 0.9], dxf=orange_dxf)
        red_rule = Rule(type='cellIs', operator='lessThan', formula=[0.7], dxf=red_dxf)
        worksheet.conditional_formatting.add('B2:C100', green_rule)
        worksheet.conditional_formatting.add('B2:C100', orange_rule)
        worksheet.conditional_formatting.add('B2:C100', red_rule)
    else: # For the detailed sheets
        if 'Status' in df.columns:
            status_col_idx = df.columns.get_loc('Status')
            for row in worksheet.iter_rows(min_row=2, max_row=len(df) + 1):
                status = row[status_col_idx].value
                fill_to_apply = None
                if status == 'MATCH': fill_to_apply = green_fill
                elif status == 'MISSING': fill_to_apply = red_fill 
                elif status == 'EXTRA': fill_to_apply = orange_fill
                
                if fill_to_apply:
                    for cell in row:
                        cell.fill = fill_to_apply

def generate_report_excel(
    flat_results: Dict, 
    decay_results: Dict,
    decay_factor: float,
    mode: str, 
    output_filename: str
):
    """
    Generates a detailed and formatted Excel report with a summary sheet.
    """
    logger.info(f"Generating comprehensive and formatted Excel report: {output_filename}")
    
    with pd.ExcelWriter(output_filename, engine='openpyxl') as writer:
        # --- Create Summary Sheet ---
        summary_data = {
            'Metric': [
                'Parameter Precision', 'Parameter Recall', 'Parameter F1-Score', '',
                'Constraint Precision', 'Constraint Recall', 'Constraint F1-Score'
            ],
            'Flat Mode (Score=1)': [
                flat_results['summary']['parameters_summary']['precision'],
                flat_results['summary']['parameters_summary']['recall'],
                flat_results['summary']['parameters_summary']['f1_score'],
                '',
                flat_results['summary']['constraints_summary']['precision'],
                flat_results['summary']['constraints_summary']['recall'],
                flat_results['summary']['constraints_summary']['f1_score'],
            ],
            f'Decay Mode (Factor={decay_factor})': [
                decay_results['summary']['parameters_summary']['precision'],
                decay_results['summary']['parameters_summary']['recall'],
                decay_results['summary']['parameters_summary']['f1_score'],
                '',
                decay_results['summary']['constraints_summary']['precision'],
                decay_results['summary']['constraints_summary']['recall'],
                decay_results['summary']['constraints_summary']['f1_score'],
            ]
        }
        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        auto_format_sheet(writer, summary_df, "Summary")

        # --- Create Detailed Sheets based on selected mode ---
        detailed_results = flat_results if mode == 'flat' else decay_results
        detailed_sheet_suffix = f"({mode.capitalize()} Mode)"
        
        param_data, constraint_data = [], []
        
        for endpoint, codes in detailed_results["endpoint_breakdown"].items():
            for code, details in codes.items():
                p_res = details["parameters"]
                for item in p_res["matches"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    param_data.append([endpoint, code, "Parameter", "MATCH", item[0], item[1], item[1], item[2], score])
                for item in p_res["extra_items"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    param_data.append([endpoint, code, "Parameter", "EXTRA", item[0], "", item[1], item[2], score])
                for item in p_res["missing_items"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    param_data.append([endpoint, code, "Parameter", "MISSING", item[0], item[1], "", item[2], score])

                c_res = details["constraints"]
                for item in c_res["matches"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    val = str(item[2])
                    constraint_data.append([endpoint, code, "Constraint", "MATCH", item[0], item[1], val, val, item[3], score])
                for item in c_res["extra_items"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    val = str(item[2])
                    constraint_data.append([endpoint, code, "Constraint", "EXTRA", item[0], item[1], "", val, item[3], score])
                for item in c_res["missing_items"]:
                    score = 1.0 if mode == 'flat' else (decay_factor ** item[-1])
                    val = str(item[2])
                    constraint_data.append([endpoint, code, "Constraint", "MISSING", item[0], item[1], val, "", item[3], score])

        param_df = pd.DataFrame(param_data, columns=["Endpoint", "Status Code", "Item Type", "Status", "Parameter Name", "Ideal Type", "Test Type", "Depth", "Score"])
        constraint_df = pd.DataFrame(constraint_data, columns=["Endpoint", "Status Code", "Item Type", "Status", "Parameter Name", "Constraint", "Ideal Value", "Test Value", "Depth", "Score"])
        
        param_sheet_name = f"Parameters {detailed_sheet_suffix}"
        constraint_sheet_name = f"Constraints {detailed_sheet_suffix}"

        param_df.to_excel(writer, sheet_name=param_sheet_name, index=False)
        auto_format_sheet(writer, param_df, param_sheet_name)
        constraint_df.to_excel(writer, sheet_name=constraint_sheet_name, index=False)
        auto_format_sheet(writer, constraint_df, constraint_sheet_name)

    logger.info(f"Successfully wrote formatted report to {output_filename}")

def print_summary(flat_results: Dict, decay_results: Dict, decay_factor: float, default_mode: str):
    """Prints a formatted summary comparing both modes to the console."""
    print("\n" + "="*80)
    print("           OpenAPI Response Evaluation Summary")
    print("="*80)
    print(f"Detailed Report Default Mode: {default_mode.capitalize()}")
    
    print("\n--- Response Parameter Metrics ---")
    print(f"{'Metric':<18} | {'Flat Mode':<15} | {'Decay Mode':<15} (Factor={decay_factor})")
    print("-" * 65)
    p_flat = flat_results['summary']['parameters_summary']
    p_decay = decay_results['summary']['parameters_summary']
    print(f"{'Precision':<18} | {p_flat['precision']:<15.3f} | {p_decay['precision']:<15.3f}")
    print(f"{'Recall':<18} | {p_flat['recall']:<15.3f} | {p_decay['recall']:<15.3f}")
    print(f"{'F1-Score':<18} | {p_flat['f1_score']:<15.3f} | {p_decay['f1_score']:<15.3f}")
    
    print("\n--- Response Constraint Metrics ---")
    print(f"{'Metric':<18} | {'Flat Mode':<15} | {'Decay Mode':<15} (Factor={decay_factor})")
    print("-" * 65)
    c_flat = flat_results['summary']['constraints_summary']
    c_decay = decay_results['summary']['constraints_summary']
    print(f"{'Precision':<18} | {c_flat['precision']:<15.3f} | {c_decay['precision']:<15.3f}")
    print(f"{'Recall':<18} | {c_flat['recall']:<15.3f} | {c_decay['recall']:<15.3f}")
    print(f"{'F1-Score':<18} | {c_flat['f1_score']:<15.3f} | {c_decay['f1_score']:<15.3f}")
    
    print("\n" + "="*80)
    print(f"Full details written to response_evaluation.xlsx and log file {LOG_FILENAME}")

# --- Main Execution ---
def run_and_get_summary(ideal_spec_path, test_spec_path, output_filename, mode='flat', decay=0.8, verbose=False):
    """
    Runs the entire response evaluation process and returns the summary statistics.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        print("Loading and preprocessing specifications for response evaluation...")
        ideal_spec = load_spec_from_file(ideal_spec_path)
        test_spec = load_spec_from_file(test_spec_path)
        
        processed_ideal = preprocess_spec(ideal_spec)
        processed_test = preprocess_spec(test_spec)

        print("\nExtracting response parameters and constraints...")
        evaluator = ResponseEvaluator(processed_ideal, processed_test)
        ideal_data = evaluator._extract_responses_by_endpoint(processed_ideal, 'ideal')
        test_data = evaluator._extract_responses_by_endpoint(processed_test, 'test')
        
        print("Running response evaluations for flat and decay modes...")
        flat_results = ResponseEvaluator.evaluate(ideal_data, test_data, mode='flat', decay_factor=decay)
        decay_results = ResponseEvaluator.evaluate(ideal_data, test_data, mode='decay', decay_factor=decay)
        
        print_summary(flat_results, decay_results, decay, mode)
        generate_report_excel(flat_results, decay_results, decay, mode, output_filename)
        
        # RETURN the summary from the 'flat' results, as it's the most direct 1-to-1 metric
        return flat_results['summary']

    except FileNotFoundError as e:
        logger.error(f"Input file not found: {e.filename}", exc_info=True)
        print(f"Error: Input file not found - {e.filename}")
        return None # Return None on failure
    except Exception as e:
        logger.critical(f"An unexpected error occurred during response evaluation: {e}", exc_info=True)
        print(f"An critical error occurred. Check log '{LOG_FILENAME}' for details.")
        return None # Return None on failure
    
def main():
    parser = argparse.ArgumentParser(description="Compare response definitions in two OpenAPI specifications.")
    parser.add_argument("ideal_spec_path", help="Path to the ideal OpenAPI spec file (JSON or YAML).")
    parser.add_argument("test_spec_path", help="Path to the test OpenAPI spec file (JSON or YAML).")
    parser.add_argument(
        "--mode", 
        choices=['flat', 'decay'], 
        default='flat',
        help="Scoring mode for the detailed report sheets. Default: flat."
    )
    parser.add_argument("--decay", type=float, default=0.8, help="Decay factor for 'decay' mode. Default: 0.8.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose DEBUG logging.")
    args = parser.parse_args()
    
    # Call the main logic function
    run_and_get_summary(
        args.ideal_spec_path,
        args.test_spec_path,
        "response_evaluation.xlsx", # Default output name when run standalone
        args.mode,
        args.decay,
        args.verbose
    )

if __name__ == "__main__":
    main()