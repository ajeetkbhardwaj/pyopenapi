#!/usr/bin/env python3
"""
Master Evaluation Script

This script orchestrates the execution of two separate OpenAPI evaluation scripts,
saves their results to a dedicated folder, and prints a final summary of
key precision and recall metrics to the console.

By default, it hides the detailed logs from the individual scripts.
Use the --verbose flag to show all logs.
"""
import argparse
import sys
import os

# Import the main logic functions from the other scripts
from response_evaluator import run_and_get_summary as run_response_evaluation
from request_evaluator import run_and_get_summary as run_other_evaluation

# --- NEW: Context Manager to Suppress Console Output ---
class SuppressOutput:
    """
    A context manager to temporarily suppress stdout.
    Useful for hiding console output from imported modules.
    """
    def __init__(self, suppress=True):
        self.suppress = suppress
        self.original_stdout = None
        self.devnull = None

    def __enter__(self):
        """If suppression is active, redirect stdout to a null device."""
        if self.suppress:
            self.original_stdout = sys.stdout
            self.devnull = open(os.devnull, 'w')
            sys.stdout = self.devnull

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Restore the original stdout."""
        if self.suppress and self.original_stdout is not None:
            # Important to close the file handle we opened
            if self.devnull:
                self.devnull.close()
            sys.stdout = self.original_stdout


# --- Configuration ---
RESPONSE_EVAL_OUTPUT_NAME = "evaluation_report_responses.xlsx"
OTHER_EVAL_OUTPUT_NAME = "evaluation_report_main.xlsx"

def print_final_summary(other_summary, response_summary):
    """Prints a formatted summary of key metrics to the console."""
    if not other_summary or not response_summary:
        print("\n[WARNING] Could not generate final summary because one of the evaluations failed.")
        return

    print("\n" + "="*80)
    print("                      OVERALL EVALUATION SUMMARY")
    print("="*80)
    
    ep_method = other_summary.get("Endpoint Methods", {})
    ep_req_param = other_summary.get("Endpoint Parameters", {})
    ep_req_constraint = other_summary.get("Parameter Constraints", {})
    ep_res_param = response_summary.get("parameters_summary", {})
    ep_res_constraint = response_summary.get("constraints_summary", {})
    
    print(f"{'Metric':<35} | {'Precision':<12} | {'Recall':<12}")
    print("-" * 65)
    print(f"{'1. Endpoint Methods':<35} | {ep_method.get('precision', 0):<12.2%} | {ep_method.get('recall', 0):<12.2%}")
    print(f"{'2. Endpoint Request Parameters':<35} | {ep_req_param.get('precision', 0):<12.2%} | {ep_req_param.get('recall', 0):<12.2%}")
    print(f"{'3. Endpoint Request Constraints':<35} | {ep_req_constraint.get('precision', 0):<12.2%} | {ep_req_constraint.get('recall', 0):<12.2%}")
    print("-" * 65)
    print(f"{'4. Endpoint Response Parameters':<35} | {ep_res_param.get('precision', 0):<12.2%} | {ep_res_param.get('recall', 0):<12.2%}")
    print(f"{'5. Endpoint Response Constraints':<35} | {ep_res_constraint.get('precision', 0):<12.2%} | {ep_res_constraint.get('recall', 0):<12.2%}")
    print("="*80)


def main():
    """
    Main function to parse arguments and orchestrate the evaluation process.
    """
    parser = argparse.ArgumentParser(description="Run all OpenAPI evaluation scripts.")
    parser.add_argument("ideal_spec_path", help="Path to the ideal OpenAPI spec file (JSON or YAML).")
    parser.add_argument("test_spec_path", help="Path to the test OpenAPI spec file (JSON or YAML).")
    parser.add_argument("--verbose", action="store_true", help="Show detailed logs from the evaluation scripts.")
    args = parser.parse_args()

    # Determine and create the output directory
    output_base_dir = os.path.dirname(os.path.abspath(args.test_spec_path))
    
    # Create a folder name from the test spec path, replacing path separators with underscores.
    # For example, a path like "f1/f2.json" will result in a folder named "f1_f2".
    path_without_ext, _ = os.path.splitext(args.test_spec_path)
    # Standardize path separators to '/' for consistent splitting
    standardized_path = path_without_ext.replace('\\', '/')
    path_parts = standardized_path.split('/')
    # Filter out empty strings (from absolute paths) or relative path navigation parts
    valid_parts = [part for part in path_parts if part and part not in ('.', '..')]

    if valid_parts:
        folder_name_from_test_spec = "_".join(valid_parts)
    else:
        # Fallback to the original behavior if the path is unusual (e.g., "/.json")
        folder_name_from_test_spec = os.path.splitext(os.path.basename(args.test_spec_path))[0]

    # Combine the base directory and the new folder name to get the full output path.
    full_output_folder_path = os.path.join(output_base_dir, folder_name_from_test_spec)
    os.makedirs(full_output_folder_path, exist_ok=True)
    
    print(f"Results will be saved in the folder: '{full_output_folder_path}'")
    
    # Define final output paths
    final_response_path = os.path.join(full_output_folder_path, RESPONSE_EVAL_OUTPUT_NAME)
    final_other_path = os.path.join(full_output_folder_path, OTHER_EVAL_OUTPUT_NAME)
    
    # --- Run Evaluations and Collect Summaries ---
    
    print("\nRunning Response Evaluation...")
    # The 'suppress' flag is the opposite of 'verbose'
    with SuppressOutput(suppress=not args.verbose):
        response_summary = run_response_evaluation(
            args.ideal_spec_path, 
            args.test_spec_path, 
            final_response_path,
            verbose=args.verbose
        )
    print("Response Evaluation complete.")
    
    print("\nRunning Other Evaluation...")
    with SuppressOutput(suppress=not args.verbose):
        other_summary = run_other_evaluation(
            args.ideal_spec_path,
            args.test_spec_path,
            final_other_path
        )
    print("Other Evaluation complete.")

    # --- Print Final Summary Log (this is always printed) ---
    print_final_summary(other_summary, response_summary)

    print("ALL EVALUATIONS COMPLETE!")
    print(f"Detailed reports saved inside folder: '{full_output_folder_path}'")

if __name__ == "__main__":
    main()