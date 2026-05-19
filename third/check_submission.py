# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy",
# ]
# ///

# HOW TO RUN:
# As a standalone script with `uv` (without the need to install numpy)
# uv run check_submission.py <student_id>
# or, if you don't have `uv` installed, after you installed `numpy` in an environment with **Python>=3.11**
# python check_submission.py <student_id>
# where <student_id> is your student ID in the format nn-nnn-nnn as on your student ID card (legi)
# and your assignment submission is in the same directory as the script, and is named 'llm_assignment_3_submission-<student_id>.zip'
# e.g. `uv run check_submission.py 12-345-678` checks the archive named 'llm_assignment_3_submission-12-345-678.zip'

import base64
import os
import sys
import numpy as np
import warnings
import re
from pathlib import Path
from zipfile import ZipFile, Path as ZipPath
import subprocess
import tempfile
import shutil

ZIP_FILENAME = "llm_assignment_3_submission-{student_id}.zip"
DECLARATION_OF_ORIGINALITY_FILENAME = "declaration_originality.pdf"

# Q1 parameters
Q1_GENS_FILENAME = "Q1_gens.npy"
Q1_GUESSES_FILENAME = "Q1_guesses.npy"
Q1_GENS_SHAPE = (60,)
Q1_GUESSES_SHAPE = (80,)
Q1_GUESSES_RANGE = (1, 4)

# Q2 parameters
Q2_API_KEY_FILENAME = "Q2_key.txt"
Q2_CODE_FILENAME = "Q2_code"
Q2_CODE_EXTENSIONS = [".py", ".ipynb"]
Q2_PVALUE_FILENAME = "Q2_pvalue.npy"
Q2_BOOL_FILENAME = "Q2_bool.npy"
Q2_ARRAYS_SHAPE = (140,)
Q2_KEY_LEN = 50

# Q3 parameters
Q3_GUESSES_FILENAME = "Q3_guesses.txt"
Q3_GUESSES_LEN = 21

# Constants
SCRIPT_FILENAME = "check_submission.py"
REPO_URL = "https://raw.githubusercontent.com/ethz-spylab/llm_lab/main/" + SCRIPT_FILENAME


class InvalidSubmissionError(Exception):
    pass


class MissingSubmissionFileWarning(Warning):
    pass


def has_specific_subdirectory(parent_path: ZipPath, subdir_name: str) -> bool:
    subdir_path = parent_path / subdir_name
    return subdir_path.exists() and subdir_path.is_dir()


def is_valid_token_urlsafe(token: str, key_len: int) -> bool:
    try:
        # Add padding if needed
        padded = token + "=" * (4 - len(token) % 4) if len(token) % 4 else token
        # Replace URL-safe chars with standard base64 chars
        padded = padded.replace("-", "+").replace("_", "/")
        # Decode and check length
        decoded = base64.b64decode(padded)
        return len(decoded) == key_len
    except Exception:
        return False


def check_q1(path: ZipPath) -> None:
    # check generations file
    gens_file = path / Q1_GENS_FILENAME
    if not gens_file.exists():
        warnings.warn(
            f"{Q1_GENS_FILENAME} not found in the submission. This part of Q1 will not be graded.",
            MissingSubmissionFileWarning,
        )
    else:
        with gens_file.open("rb") as f:
            gens = np.load(f)
            if not np.issubdtype(gens.dtype, np.str_):
                raise InvalidSubmissionError(
                    f"{Q1_GUESSES_FILENAME} should contain strings only, got {gens.dtype}."
                )
            if gens.shape != Q1_GENS_SHAPE:
                raise InvalidSubmissionError(
                    f"{Q1_GENS_FILENAME} should contain exactly {Q1_GENS_SHAPE} elements, got {gens.shape}."
                )

    # check guesses file
    guesses_file = path / Q1_GUESSES_FILENAME
    if not guesses_file.exists():
        warnings.warn(
            f"{Q1_GUESSES_FILENAME} not found in the submission. This part of Q1 will not be graded.",
            MissingSubmissionFileWarning,
        )
        return
    with guesses_file.open("rb") as f:
        guesses = np.load(f)
        if not np.issubdtype(guesses.dtype, np.integer):
            raise InvalidSubmissionError(
                f"{Q1_GUESSES_FILENAME} should contain integers only, got {guesses.dtype}."
            )
        if not (
            np.all(guesses >= Q1_GUESSES_RANGE[0])
            and np.all(guesses <= Q1_GUESSES_RANGE[1])
        ):
            raise InvalidSubmissionError(
                f"{Q1_GUESSES_FILENAME} should contain integers between {Q1_GUESSES_RANGE[0]} and {Q1_GUESSES_RANGE[1]} (both inclusive) only."
            )
        if guesses.shape != Q1_GUESSES_SHAPE:
            raise InvalidSubmissionError(
                f"{Q1_GUESSES_FILENAME} should have shape {Q1_GUESSES_SHAPE}, got {guesses.shape}."
            )


def check_q2(path: ZipPath) -> None:
    api_key_file = path / Q2_API_KEY_FILENAME
    if not api_key_file.exists():
        warnings.warn(
            f"{Q2_API_KEY_FILENAME} not found in the submission. In the current state, Q2 will not be graded.",
            MissingSubmissionFileWarning,
        )
        return
    with api_key_file.open() as f:
        api_key = f.read().strip()
        if not is_valid_token_urlsafe(api_key, Q2_KEY_LEN):
            raise InvalidSubmissionError(
                f"{Q2_API_KEY_FILENAME} should contain a base64-encoded string of {Q2_KEY_LEN} bytes."
            )

    # Check the code file
    code_file_exists = False
    for ext in Q2_CODE_EXTENSIONS:
        code_file = path / f"{Q2_CODE_FILENAME}{ext}"
        if code_file.exists():
            if code_file_exists:
                raise InvalidSubmissionError(
                    f"Multiple {Q2_CODE_FILENAME} files found, only one is allowed."
                )
            code_file_exists = True

    if not code_file_exists:
        warnings.warn(
            f"No {Q2_CODE_FILENAME} file found. Please submit a file with one of these extensions: {', '.join(Q2_CODE_EXTENSIONS)}. In the current state, Q2 will not be graded.",
            MissingSubmissionFileWarning,
        )
        return

    # control p-values file
    for file in [Q2_PVALUE_FILENAME, Q2_BOOL_FILENAME]:
        array_file = path / file
        if not array_file.exists():
            warnings.warn(
                f"{file} not found. This part of Q2 will not be graded.",
                MissingSubmissionFileWarning,
            )
            continue
        with array_file.open("rb") as f:
            array = np.load(f)
            if not np.issubdtype(array.dtype, np.integer):
                raise InvalidSubmissionError(
                    f"{file} should contain integers only, got {array.dtype}."
                )
            if array.shape[0] > Q2_ARRAYS_SHAPE[0]:
                raise InvalidSubmissionError(
                    f"{file} should have at most {Q2_ARRAYS_SHAPE} elements, got {array.shape}."
                )


def check_q3(path: ZipPath) -> None:
    # check generations file
    guesses_file = path / Q3_GUESSES_FILENAME
    if not guesses_file.exists():
        warnings.warn(
            f"{Q3_GUESSES_FILENAME} not found in the submission. Q3 will not be graded.",
            MissingSubmissionFileWarning,
        )
        return
    with guesses_file.open() as f:
        guesses = f.readlines()
        if len(guesses) != Q3_GUESSES_LEN:
            raise InvalidSubmissionError(
                f"{Q3_GUESSES_FILENAME} should contain exactly {Q3_GUESSES_LEN} elements, got {len(guesses)}."
            )


def check_submission(zip_file_path: Path, student_id: str) -> None:
    if not os.path.exists(zip_file_path):
        raise InvalidSubmissionError(f"Submission file {zip_file_path} not found.")
    with ZipFile(zip_file_path, "r") as zip_file:
        root_path = ZipPath(zip_file)

        if has_specific_subdirectory(root_path, zip_file_path.stem):
            root_path = root_path / zip_file_path.stem

        if not (root_path / DECLARATION_OF_ORIGINALITY_FILENAME).exists():
            raise InvalidSubmissionError(
                f"{DECLARATION_OF_ORIGINALITY_FILENAME} not found in the submission."
            )

        check_q1(root_path)
        check_q2(root_path)
        check_q3(root_path)


def check_script_version():
    """Checks if the current script is the latest version from the repository."""
    print(f"Checking for the latest version of {SCRIPT_FILENAME}...")
    # Check if wget and diff are available
    if not shutil.which("wget") or not shutil.which("diff"):
        print("Warning: 'wget' or 'diff' command not found. Cannot check for script updates.")
        return

    current_script_path = Path(__file__).resolve()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as temp_file:
        temp_file_path = Path(temp_file.name)

    try:
        # Download the latest script version
        wget_cmd = ["wget", "-q", "-O", str(temp_file_path), REPO_URL]
        wget_result = subprocess.run(wget_cmd, capture_output=True, text=True)

        if wget_result.returncode != 0:
            print(f"Warning: Failed to download the latest script version from {REPO_URL}. Error: {wget_result.stderr}")
            return

        diff_cmd = ["diff", "-u", str(current_script_path), str(temp_file_path)]
        diff_result = subprocess.run(diff_cmd, capture_output=True, text=True)

        # diff exits with 1 if files differ, 0 if identical, >1 if error
        if diff_result.returncode == 1:
            print("------------------------- SCRIPT OUTDATED -----------------------------")
            print("Warning: You are using an outdated version of this script.")
            print(f"Please download the latest version from: {REPO_URL}")
            print("\nDifferences found:")
            print(diff_result.stdout) # Print the actual diff
            print("--------------------------------------------------------------------------")
        elif diff_result.returncode == 0:
            print("You are using the latest version of the script.")
        else:
            print(f"Warning: Error running diff command: {diff_result.stderr}")

    except Exception as e:
        print(f"Warning: An error occurred while checking for script updates: {e}")
    finally:
        # Clean up the temporary file
        if temp_file_path.exists():
            temp_file_path.unlink()


def main(student_id: str) -> None:
    # Check script version first
    check_script_version()

    zip_file_path = Path(ZIP_FILENAME.format(student_id=student_id))
    with warnings.catch_warnings(
        record=True, category=MissingSubmissionFileWarning
    ) as warnings_list:
        check_submission(zip_file_path, student_id)
        if not warnings_list:
            print("Everything is ok!")
            return

        print("!!! Careful, some warnings were raised:")
        for warning in warnings_list:
            print(warning.message)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python check_submission.py student_id\n - student_id: the student ID on your legi (looks like 'nn-nnn-nnn')"
        )
        sys.exit(1)
    student_id = sys.argv[1]
    # Check if the student ID matches the pattern 'XX-XXX-XXX' where X is any digit
    if not bool(re.match(r"^\d{2}-\d{3}-\d{3}$", student_id)):
        print("Student ID must be in the 'nn-nnn-nnn' format.")
        sys.exit(1)
    main(student_id)
