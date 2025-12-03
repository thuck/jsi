# JSI (Jellyfin Spotify Importer)

JSI is a command line software that reads the export of your ["Account Data"](https://community.spotify.com/t5/Your-Library/How-do-I-export-my-playlists/td-p/5517422) from Spotify and imports the playlists on Jellyfin.  
It also supports reading a csv file to create a playlist.

If a playlist already exists and JSI detects a new track it will append to the existing playlist.  
JSI doesn't try to keep playlists in sync, it won't remove tracks included after the playlist creation.  
JSI doesn't download any music, it doesn't connect to any server besides your Jellyfin instance.

## Usage

### Spotify import

`jsi --spotify --user {USERNAME} --token "{API_TOKEN}" --url "{INSTANCE_URL}" --any-album '{PATH_FOR_PLAYLIST_JSON}'`

### CSV import

`jsi --csv --user {USERNAME} --token "{API_TOKEN}" --url "{INSTANCE_URL}" --any-album '{PATH_FOR_CSV_FILE}'`

## Installation

Clone this repository and run: `uv tool install . -e`  
You're going to need [uv](https://docs.astral.sh/uv/).

## Options

```
Usage: jsi [OPTIONS] FILENAME

Options:
  --spotify                       File exported from spotify
  --csv                           File needs to contain: trackName,
                                  artistName, albumName
  --token TEXT                    API token  [required]
  --user TEXT                     Username  [required]
  --public                        Sets playlist to public on creation
  --any-album                     Search track in any album
  --url TEXT                      Jellyfin url instance  [required]
  --fuzz INTEGER RANGE            Tolerance for the fuzzy match, higher values
                                  means less tolerance  [80<=x<=100]
  --dry-run                       Avoids playlist creation
  --log-level [critical|error|warning|info|debug]
                                  Log Level
  --skip-tls                      Skip tls verification
  --help                          Show this message and exit.
```
