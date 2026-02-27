#!/bin/env python3
"""
[
    [
        {
            "title": "Patrística Latina, Volume 1",
            "description": "",
            "link": "https://drive.google.com/file/d/1S2oA3GefC1otfFlwVVne_U_mnrGnnxIZ/view?usp=drive_link"
        },
"""

import os
import gdown
from gdown import parse_url
import json
import time
from functools import wraps
import selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def retry_on_exception(max_retries=5, delay=2, exceptions=(Exception,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    print(f"Attempt {attempt} failed: {e}")
                    if attempt == max_retries:
                        raise
                    time.sleep(delay)
        return wrapper
    return decorator

@retry_on_exception(max_retries=1, delay=2)
def download_file(url):
    """
    Download a file from Google Drive using gdown.
    
    :param url: The URL of the file to download.
    :param output: The path where the file will be saved.
    """
    print(f"Downloading {url}...")
    outputfile = gdown.download(url, None, quiet=False, fuzzy=True, resume=True)

    if outputfile:
        print(f"Downloaded {outputfile} successfully.")
    else:
        print(f"Failed to download {url}.")
        raise Exception(f"Download failed for {url}")


def main():
    browser_options = Options()
    download_dir = os.getcwd()
    prefs = {"download.default_directory": download_dir}
    browser_options.add_experimental_option("prefs", prefs)
    browser = webdriver.Chrome(options=browser_options)
    
    # Load the JSON data
    with open('drivegoogle2.json', 'r') as file:
        data = json.load(file)
        
    for item in data:
        for book in item:
            title = book.get("title", "Unknown Title")
            link = book.get("link", "")

            # "title": "Patrística Latina, Volume 1", = filename PL001.pdf
            # "title": "Patristica Graeca, Volume 1", = filename PG001.pdf
            # "title": "Patristica Orientalis. Volume 22.", = filename PO022.pdf

            # Extract the title and check if the file already exists
            # extract the number from the title
            title_lower = title.lower().strip()
            if "latina" in title_lower:
                prefix = "PL"
            elif "greca" in title_lower or "graeca" in title_lower or "græca" in title_lower:
                prefix = "PG"
            elif "orientalis" in title_lower:
                prefix = "PO"
            else:
                prefix = "UN"

            number = ''.join(filter(str.isdigit, title_lower))
            filename = f"{prefix}{number.zfill(3)}.pdf"
            if os.path.exists(filename):
                print(f"File {filename} already exists. Skipping download.")
                continue
            print(f"Preparing to download {title} as {filename}...")

            try:
                print(f"Processing {title}...")
                download_file(link)
            except Exception as e:
                print(f"Error downloading {title}: {e}")

                gdrive_file_id, is_gdrive_download_link = parse_url.parse_url(link, False)
                download_url = link
                if gdrive_file_id and not is_gdrive_download_link:
                    print(f"Attempting to download {title} using file ID {gdrive_file_id}...")
                    download_url = f"https://drive.google.com/uc?id={gdrive_file_id}"

                browser.get(download_url)

                # uc-download-link
                """ try:
                    download_button = browser.find_element(By.ID, "uc-download-link")
                    download_button.click()
                    print(f"Clicked download button for {title}.")
                except selenium.common.exceptions.NoSuchElementException:
                    print(f"Download button not found for {title}. Trying alternative method.")
                    # Try to find the link directly
                    download_link = browser.find_element(By.XPATH, "//a[contains(@href, 'uc?export=download')]")
                    download_link.click()
                    print(f"Clicked alternative download link for {title}.") """


if __name__ == "__main__":
    main()
# This script downloads files from Google Drive using gdown based on a JSON configuration file.
# The JSON file should contain a list of dictionaries with "title" and "link" keys.
# The script reads the JSON file, extracts the download links, and downloads each file.
# The downloaded files will be saved in the current working directory.

