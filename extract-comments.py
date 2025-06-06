"""Script for scraping all the comments on the following page (and paginated next pages)
https://www.regulations.gov/docket/FDA-2025-N-1146/comments

Modified to:
- Create a new CSV file every 500 comments with incremented file numbers
- Better handle rate limiting
- Resume scraping from where it left off

** Python version:
Any supported Python version should be fine. Tested on Python 3.11, 3.12 and 3.13.

** How to run:
0. Optionally, activate a virtual environment.

1. Install all the dependencies from `requirements.txt`:
python3 -m pip install -r requirements.txt

2. Execute the file:
python3 regulations_fda_scraping.py
"""

import csv
import json
import random
import time
import os
import sys
from datetime import datetime
from typing import Dict, Optional, Set, Tuple, Union, List

import requests

# API key for `api.regulations.gov`. This key is static and same for all users.
# So no need to get a new key or update.
API_KEY = r"5F20SbTVakeYfU9i5gX1dxx96sw4KELUQxAHhcHa"
API_URL = r"https://api.regulations.gov/v4"
API_COMMENTS_URL = rf"{API_URL}/comments"


# The following dates can be changed to get data for a certain range.
# The dates must be in the format "<year>-<month>-<day>", as shown below.
# Start date for the filtering
START_DATE = "2025-04-19"
# End date for the filtering
END_DATE = "2025-05-18"

# Number of comments per CSV file before creating a new one
COMMENTS_PER_FILE = 500

# Base name for CSV files
BASE_CSV_FILENAME = rf"regulations_fda_scraping_result_{datetime.now().strftime('%Y-%m-%d_%H-%M')}"

# File to store already processed comment IDs for resuming scraping
PROCESSED_IDS_FILE = "processed_comment_ids.txt"

# Store already processed comment IDs
processed_comment_ids = set()

# Initialize the file counter
file_counter = 1

# Initialize the comment counter for the current file
current_file_comment_count = 0


API_REQUEST_HEADERS = {
    "Host": "api.regulations.gov",
    "Accept": "application/vnd.api+json",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": "https://www.regulations.gov/",
    "X-Api-Key": API_KEY,
    "Origin": "https://www.regulations.gov",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-GPC": "1",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "TE": "trailers",
}


def do_sleep(duration: Union[Tuple[int], int] = (1, 5)) -> None:
    """Sleep for the input `duration. The input must be an integer or
    a two-element tuple. If the input is a two element tuple, pick a
    random number between them, and sleep for that duration.

    The main purpose of this function is to have a delay between
    successive requests to URLs.
    """

    if isinstance(duration, tuple):
        duration = (duration[0], duration[1] + 1)
        duration = random.choice(range(*duration))

    time.sleep(duration)


def get_random_user_agent() -> str:
    """Return a random user agent to be used in the API request."""

    chrome = r"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.4692.99 Safari/537.36".format  # noqa

    firefox = r"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:95.0) Gecko/20100101 Firefox/{}.0".format  # noqa

    user_agents = []

    for i in range(120, 127):
        user_agents.append(chrome(i))
        user_agents.append(firefox(i))

    return random.choice(user_agents)


def get_requests_response(
    url: str,
    *,
    method: str = "get",
    headers: Dict[str, str] = API_REQUEST_HEADERS,
    params: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, str]] = None,
    payload_json: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    add_random_delay: bool = False,
    add_random_user_agent: bool = True,
    num_of_tries: int = 3,  # Increased from 1 to 3
    successful_http_status_codes: Tuple[int] = (200, 201, 202),
    rate_limit_status_codes: Tuple[int] = (429,),
    rate_limit_wait_seconds: int = 10 * 60,  # Increased to 10 minutes
    verify_https: bool = True,
    allow_redirects: bool = True,
) -> Optional[requests.Response]:
    """Send a request to the input `url` and return the response object."""

    if num_of_tries < 1:
        raise ValueError(
            f"`num_of_tries` must be 1 or more but {num_of_tries} was passed."
        )

    resp = None

    num_of_try = 1

    while True:
        if num_of_try > num_of_tries:
            break

        if num_of_try > 1:
            print(f"URL {url}, try num: {num_of_try}...")

        requests_kwargs = {
            "timeout": timeout,
            "verify": verify_https,
            "allow_redirects": allow_redirects,
        }

        headers = headers.copy()
        if add_random_user_agent:
            headers["User-Agent"] = get_random_user_agent()
        requests_kwargs.update({"headers": headers})

        if params:
            requests_kwargs.update({"params": params})

        if payload:
            requests_kwargs.update({"data": payload})

        if payload_json:
            requests_kwargs.update({"json": payload_json})

        requests_method = getattr(requests, method)

        if add_random_delay:
            do_sleep((3, 7))  # Increased delay between requests

        try:
            resp = requests_method(url, **requests_kwargs)
        except requests.exceptions.Timeout:
            print(
                f"Timed out ({timeout} seconds) while getting response from URL {url}"
            )
            num_of_try += 1
            continue
        except Exception as e:
            print(f"Exception in URL {url}: {repr(e)}")
            num_of_try += 1
            continue

        status_code = resp.status_code

        if status_code in successful_http_status_codes:
            return resp

        # We don't increment the `num_of_try` counter in case of rate-limiting
        if status_code in rate_limit_status_codes:
            print(
                f"Rate limit reached. Retrying in {rate_limit_wait_seconds} seconds..."
            )
            # Save progress before waiting
            save_processed_ids()
            do_sleep(rate_limit_wait_seconds)
            continue

        print(
            f"Response status code {status_code} not in the "
            f"successful http status codes {successful_http_status_codes}."
        )
        num_of_try += 1
        continue

    return resp


def load_processed_ids():
    """Load already processed comment IDs from file."""
    global processed_comment_ids
    
    if os.path.exists(PROCESSED_IDS_FILE):
        with open(PROCESSED_IDS_FILE, "r") as f:
            processed_comment_ids = set(line.strip() for line in f)
        print(f"Loaded {len(processed_comment_ids)} already processed comment IDs.")


def save_processed_ids():
    """Save processed comment IDs to file."""
    with open(PROCESSED_IDS_FILE, "w") as f:
        for comment_id in processed_comment_ids:
            f.write(f"{comment_id}\n")


def get_comment_ids(page_size: int = 250) -> List[str]:
    """Process all the comment list pages and return comment IDs found as an ordered list."""

    if not (5 <= page_size <= 250):
        print(f"page_size must be between 5 and 250, but {page_size} was provided.")
        return []

    print("\nGetting comment IDs from all pages...\n")

    comment_ids = []
    comment_ids_set = set()  # For faster lookup

    list_request_params = {
        r"filter[commentOnId]": "09000064b8d17e62",
        r"filter[postedDate][ge]": START_DATE,
        r"filter[postedDate][le]": END_DATE,
        "sort": "-postedDate",
        r"page[size]": page_size,
    }

    page_num = 1

    while True:
        print(f"Processing page {page_num} of the list URL...")

        list_request_params[r"page[number]"] = str(page_num)

        response = get_requests_response(
            url=API_COMMENTS_URL,
            method="get",
            params=list_request_params,
            add_random_delay=True,
            add_random_user_agent=True,
            num_of_tries=3,  # Increased retries
        )
        
        if not response:
            print(f"Failed to get response for page {page_num}. Stopping.")
            break
            
        try:
            response_content = response.json()
            response_data = response_content["data"]
            has_next_page = response_content["meta"]["hasNextPage"]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Invalid response. Error while processing response data: {repr(e)}")
            break

        for comment in response_data:
            comment_id = comment["id"]
            if comment_id in comment_ids_set:
                print(
                    f"Comment ID {comment_id} is already added."
                    " Skipping this one as duplicate."
                )
                continue
            comment_ids.append(comment_id)
            comment_ids_set.add(comment_id)

        if has_next_page:
            page_num += 1
        else:
            break

    print(f"\nTotal comment IDs found: {len(comment_ids)}\n")

    return comment_ids


def get_comment_detail_data(comment_id: str) -> Dict[str, str]:
    """Get details for a specific comment."""
    
    do_sleep((2, 4))  # Increased delay

    comment_detail_url = rf"{API_COMMENTS_URL}/{comment_id}"
    comment_detail_request_params = {
        "include": "attachments",
    }

    response = get_requests_response(
        url=comment_detail_url,
        method="get",
        params=comment_detail_request_params,
        add_random_delay=False,
        add_random_user_agent=True,
        num_of_tries=3,  # Increased retries
    )
    
    if not response:
        print(f"Failed to get details for comment ID {comment_id}.")
        return None

    try:
        comment_response_content = response.json()["data"]
        comment_attributes = comment_response_content["attributes"]
        tracking_number = comment_attributes.get("trackingNbr", "") or ""
        country = comment_attributes.get("country", "") or ""
        state_or_province = comment_attributes.get("stateProvinceRegion", "") or ""
        zip_code = comment_attributes.get("zip", "") or ""
        document_category = comment_attributes.get("category", "") or ""
        document_subtype = comment_attributes.get("subtype", "") or ""
        received_date = str(comment_attributes.get("receiveDate", "")).partition("T")[0]
        title = comment_attributes.get("title", "") or ""
        object_id = comment_attributes.get("objectId", "") or ""
        agency_id = comment_attributes.get("agencyId", "") or ""
        docket_id = comment_attributes.get("docketId", "") or ""
        open_for_comment = comment_attributes.get("openForComment", "")
        comment_on_document_id = comment_attributes.get("commentOnDocumentId", "") or ""
        withdrawn = comment_attributes.get("withdrawn", "")
        restrict_reason = comment_attributes.get("restrictReason", "") or ""
        restrict_reason_type = comment_attributes.get("restrictReasonType", "") or ""
        comment = comment_attributes.get("comment", "") or ""
    except (json.JSONDecodeError, KeyError) as e:
        print(
            "Invalid response. Could not process detail URL response for"
            f" comment ID {comment_id}. Error: {repr(e)}"
        )
        return None

    return dict(
        comment_id=comment_id,
        tracking_number=tracking_number,
        country=country,
        state_or_province=state_or_province,
        zip_code=zip_code,
        document_category=document_category,
        document_subtype=document_subtype,
        received_date=received_date,
        url=comment_detail_url,
        title=title,
        object_id=object_id,
        agency_id=agency_id,
        docket_id=docket_id,
        open_for_comment=open_for_comment,
        comment_on_document_id=comment_on_document_id,
        withdrawn=withdrawn,
        restrict_reason=restrict_reason,
        restrict_reason_type=restrict_reason_type,
        comment=comment,
    )


def create_new_csv(file_counter):
    """Create a new CSV file with headers."""
    output_file = f"{BASE_CSV_FILENAME}_{file_counter}.csv"
    print(f"\nCreating new CSV file: {output_file}\n")
    
    f = open(output_file, "wt")
    writer = csv.DictWriter(
        f=f,
        fieldnames=[
            "comment_id",
            "tracking_number",
            "country",
            "state_or_province",
            "zip_code",
            "document_category",
            "document_subtype",
            "received_date",
            "url",
            "title",
            "object_id",
            "agency_id",
            "docket_id",
            "open_for_comment",
            "comment_on_document_id",
            "withdrawn",
            "restrict_reason",
            "restrict_reason_type",
            "comment",
        ],
    )
    writer.writeheader()
    return f, writer, output_file


def main() -> None:
    global file_counter, current_file_comment_count, processed_comment_ids
    
    print(f"Getting comments between {START_DATE} and {END_DATE}")
    print(f"Will create a new CSV file every {COMMENTS_PER_FILE} comments")
    
    # Load already processed IDs
    load_processed_ids()
    
    # Find the highest file counter from existing files to continue numbering
    for filename in os.listdir('.'):
        if filename.startswith(BASE_CSV_FILENAME) and filename.endswith('.csv'):
            try:
                counter = int(filename.split('_')[-1].split('.')[0])
                file_counter = max(file_counter, counter + 1)
            except (ValueError, IndexError):
                pass
    
    comment_ids = get_comment_ids()
    if not comment_ids:
        return None

    _total_comment_ids = len(comment_ids)
    comments_to_process = [cid for cid in comment_ids if cid not in processed_comment_ids]
    
    if not comments_to_process:
        print("All comments have already been processed. Nothing to do.")
        return
    
    print(f"Total comments to process: {len(comments_to_process)} out of {_total_comment_ids}")
    
    # Create the first CSV file
    f, writer, current_file = create_new_csv(file_counter)

    try:
        for _idx, comment_id in enumerate(comments_to_process, start=1):
            if comment_id in processed_comment_ids:
                continue
                
            print(
                f"Getting data for comment ID {comment_id}"
                f" ({_idx}/{len(comments_to_process)})..."
            )
            
            # Check if we need to create a new file
            if current_file_comment_count >= COMMENTS_PER_FILE:
                # Close the current file
                f.close()
                print(f"\nCompleted file {current_file} with {current_file_comment_count} comments.\n")
                
                # Increment file counter and reset comment counter
                file_counter += 1
                current_file_comment_count = 0
                
                # Create a new file
                f, writer, current_file = create_new_csv(file_counter)
            
            # Get comment details
            comment_detail_data = get_comment_detail_data(comment_id=comment_id)
            
            if comment_detail_data:
                writer.writerow(rowdict=comment_detail_data)
                f.flush()  # Flush after each write to ensure data is saved
                current_file_comment_count += 1
                processed_comment_ids.add(comment_id)
                
                # Save progress periodically (every 50 comments)
                if len(processed_comment_ids) % 50 == 0:
                    save_processed_ids()
                    print(f"Progress saved: {len(processed_comment_ids)} comments processed so far.")
    
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Saving progress...")
    except Exception as e:
        print(f"\nError occurred: {repr(e)}. Saving progress...")
    finally:
        # Close the current file
        if f:
            f.close()
            
        # Save processed IDs
        save_processed_ids()
        
        print(f"\nCompleted file {current_file} with {current_file_comment_count} comments.")
        print(f"Total comments processed: {len(processed_comment_ids)}")
        print("\nDone!\n")


if __name__ == "__main__":
    main()