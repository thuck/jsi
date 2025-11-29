import csv
import json
import logging
import sys
from functools import cache
from pathlib import Path

import click
import requests
import urllib3
from rapidfuzz import fuzz, utils

USER = ""
urllib3.disable_warnings(category=urllib3.exceptions.InsecureRequestWarning)


@click.pass_context
def jellyfin(ctx, path, method=requests.get, payload={}, params={}) -> dict:
    headers = {"Authorization": f"MediaBrowser Token={ctx.params['token']}"}
    url = ctx.params["url"]
    result = {}
    try:
        response = method(
            f"{url}{path}",
            headers=headers,
            params=params,
            json=payload,
            verify=not ctx.params.get("skip_tls"),
        )
    except requests.exceptions.SSLError as e:
        logging.error(f"SSL: {e}")
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection: {e}")
        sys.exit(1)

    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError:
        if not response.ok:
            logging.error(f"Jellyfin status code: {response.status_code}")
            sys.exit(1)
    return result


def get_user(username: str) -> str:
    response = jellyfin("/Users")
    id = None
    for user in response:
        if user.get("Name") == username:
            id = user.get("Id")
    if not id:
        logging.error(f"User doesn't exist: {username}")
        sys.exit(1)
    return id


@cache
def get_artist(name: str) -> dict:
    response = jellyfin(f"/Artists/{name}")
    return response


@cache
def get_all_albums(artist: str) -> list:
    search = {
        "parentId": get_artist(artist).get("Id"),
        "includeItemTypes": "MusicAlbum",
        "sortBy": "ProductionYear",
        "sortOrder": "Ascending",
    }
    response = jellyfin("/Items", params=search)
    return response.get("Items", [])


@cache
@click.pass_context
def get_all_tracks(ctx, artist: str, album: str) -> dict:
    response = {}
    albums = get_all_albums(artist)

    for item in albums:
        if fuzz.QRatio(
            item.get("Name"),
            album,
            processor=utils.default_process,
        ) == ctx.params.get("fuzz"):
            response = jellyfin(
                "/Items",
                params={
                    "parentId": item.get("Id"),
                    "recursive": True,
                    "mediaTypes": "Audio",
                    "limit": 100,
                },
            ).get("Items", {})

    return response


def get_playlist(name: str) -> dict:
    response = jellyfin(f"/Users/{USER}/Items")
    playlist_folder = {
        "ParentId": item.get("Id")
        for item in response.get("Items", {})
        if item.get("Type") == "ManualPlaylistsFolder"
    }
    response = jellyfin(f"/Users/{USER}/Items", params=playlist_folder)

    playlist = [
        playlist
        for playlist in response.get("Items", [])
        if playlist.get("Name") == name
    ]
    return playlist[0] if playlist else {}


@click.pass_context
def get_music(ctx, track: dict) -> dict:
    if tracks := get_all_tracks(track["artistName"], track["albumName"]):
        for item in tracks:
            if fuzz.QRatio(
                item.get("Name"),
                track["trackName"],
                processor=utils.default_process,
            ) == ctx.params.get(
                "fuzz"
            ):  # Jellyfin can't find songs with special chars sometimes
                # fuzz.QRatio helps to solve the special chars issue
                logging.info(
                    f"Track found: {track['trackName']} Artist: {track['artistName']} Album: {track['albumName']}"
                )
                return item

    if ctx.params.get("any_album"):
        albums = get_all_albums(track["artistName"])
        for album in albums:
            for item in get_all_tracks(track["artistName"], album.get("Name")):
                if fuzz.QRatio(
                    item.get("Name"),
                    track["trackName"],
                    processor=utils.default_process,
                ) == ctx.params.get("fuzz"):
                    logging.info(
                        f"Track found: {track['trackName']} Artist: {track['artistName']} Album: {item.get('Album')} AlbumOriginal: {track['albumName']}"
                    )
                    return item

    logging.warning(
        f"Track not found: {track['trackName']} Artist: {track['artistName']} Album: {track['albumName']}"
    )
    return {}


def get_playlist_items(name: str) -> set:
    ids = set()
    if playlist := get_playlist(name):
        response = jellyfin(
            f"/Playlists/{playlist.get('Id')}/Items", params={"userId": USER}
        )
        ids = {item.get("Id") for item in response.get("Items", [])}
    return ids


@click.pass_context
def create_playlist(ctx, name: str, tracks: list):
    tracks_jellyfin = [get_music(track).get("Id") for track in tracks]
    if tracks := [
        track for track in tracks_jellyfin if track
    ]:  # Avoid creation of empty playlists
        if playlist := get_playlist(name):
            existing_tracks = get_playlist_items(name)
            if new_tracks := set(tracks).difference(existing_tracks):
                logging.info(f"Playlist update: {name}")
                if not ctx.params.get("dry_run"):
                    jellyfin(
                        f"/Playlists/{playlist.get('Id')}/Items",
                        method=requests.post,
                        params={
                            "ids": ",".join(new_tracks),
                            "userId": USER,
                        },
                    )
                else:
                    logging.info(
                        f"Dry run: /Playlists/{playlist.get('Id')}/Items | 'ids': {','.join(new_tracks)}"
                    )

            else:
                logging.info(
                    f"Playlist skip update: {name} no new tracks found on jellyfin"
                )
        else:
            logging.info(f"Playlist creating: {name}")
            if not ctx.params.get("dry_run"):
                jellyfin(
                    "/Playlists",
                    payload={
                        "Name": name,
                        "MediaType": "Audio",
                        "isPublic": ctx.params["private"],
                        "userId": USER,
                        "Ids": tracks,
                    },
                    method=requests.post,
                )
            else:
                logging.info(f"Dry run: /Playlists | 'ids': {','.join(tracks)}")
    else:
        logging.info(f"Playlist skip creation: {name} no tracks found on jellyfin")


def spotify_parser(content: dict) -> dict:
    playlists = content.get("playlists", [])
    result = {
        playlist.get("name"): [item.get("track") for item in playlist.get("items")]
        for playlist in playlists
        if playlist.get("items")
    }
    return result


@click.command()
@click.argument("filename")
@click.option("--spotify", is_flag=True, help="File exported from spotify")
@click.option(
    "--csv",
    "_csv",
    is_flag=True,
    help="File needs to contain: trackName, artistName, albumName",
)
@click.option("--token", required=True, help="API token")
@click.option("--user", required=True, help="Username")
@click.option("--private", is_flag=True, help="Sets playlist to private on creation")
@click.option(
    "--any-album",
    is_flag=True,
    help="Search track in any album",
)
@click.option("--url", required=True, help="Jellyfin url instance")
@click.option(
    "--fuzz",
    type=click.IntRange(80, 100),
    default=100,
    help="Tolerance for the fuzzy match, higher values means less tolerance",
)
@click.option("--dry-run", is_flag=True, help="Avoids playlist creation")
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(["critical", "error", "warning", "info", "debug"]),
    help="Log Level",
)
@click.option("--skip-tls", is_flag=True, help="Skip tls verification")
def main(
    filename: str,
    spotify: bool,
    _csv: bool,
    token: str,
    user: str,
    private: bool,
    any_album: bool,
    url: str,
    fuzz: int,
    dry_run: bool,
    log_level: str,
    skip_tls: bool,
):
    global USER
    logging.basicConfig(level=log_level.upper(), format="%(levelname)s: %(message)s")
    USER = get_user(user)
    p = Path(filename).expanduser()
    with p.open() as _f:
        if spotify:
            try:
                sp_playlists = json.loads(_f.read())
            except json.decoder.JSONDecodeError:
                logging.error("Spotify: cannot decode json file")
                sys.exit(1)
            playlists = spotify_parser(sp_playlists)

            for playlist, tracks in playlists.items():
                create_playlist(playlist, tracks)
        elif _csv:
            tracks = csv.DictReader(
                _f,
            )
            if all(
                field in (tracks.fieldnames or [])
                for field in ["trackName", "artistName", "albumName"]
            ):
                create_playlist(Path(filename).stem, [row for row in tracks])
            else:
                logging.error("CSV: cannot find field(s)")
                sys.exit(1)


if __name__ == "__main__":
    main(prog_name="jsi")
