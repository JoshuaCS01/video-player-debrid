# Home Media Manager

A lightweight Flask application for managing and launching media from multiple sources through a single web interface.

The application currently supports media stored in a local/network SMB share as well as media available through Real-Debrid. It provides a browser-based interface for viewing media, launching playback, browsing available Real-Debrid torrents, and bookmarking items for later access.

## Features

* Browse a saved media playlist through a web interface
* Retrieve available torrents from the Real-Debrid API
* Generate unrestricted Real-Debrid playback links
* Bookmark Real-Debrid items to a local JSON playlist
* Launch bookmarked Real-Debrid media
* Access media stored on an SMB/network share
* Serve local network media through Flask
* Store basic media metadata such as titles and associated stars

## Tech Stack

* Python
* Flask
* HTML
* JSON
* Requests
* Real-Debrid REST API
* SMB/network storage

## Running the Application

Install the required Python packages:

```bash
pip install flask requests
```

Then run:

```bash
python app.py
```

The Flask server runs on:

```text
http://localhost:8080
```

Because the application binds to `0.0.0.0`, it can also be accessed by other devices on the local network using the host computer's local IP address.

## Configuration

The application requires configuration for:

* Real-Debrid API credentials
* Real-Debrid URLs
* SMB/network media location

API credentials should not be committed to source control. Use environment variables or another local configuration mechanism when sharing or publishing the project.

Example:

```python
import os

REAL_DEBRID_TOKEN = os.environ.get("REAL_DEBRID_TOKEN")
```

## Current Limitations

This is a personal project under active development.

The current implementation intentionally remains relatively lightweight and has several areas that could be improved, including:

* Configuration management
* Error handling
* Media metadata management
* Duplicate detection
* Network path handling
* Separation of application and API logic
* Automated testing
* User-facing error messages

## Purpose

The project was created as a personal media-management tool for accessing media available from different sources through one interface. It also serves as an experimental project for working with Flask, REST APIs, network storage, JSON-based persistence, and media playback workflows.
