import csv
import json
import logging
import sys
from functools import cache
from pathlib import Path

import click
import urllib3
from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.exceptions import HTTPException
from rapidfuzz import fuzz, utils

urllib3.disable_warnings(category=urllib3.exceptions.InsecureRequestWarning)


def get_user_id(client, username: str) -> str:
    response = client.get_users()
    id = None
    for user in response:
        if user.get("Name") == username:
            id = user.get("Id")
    if not id:
        logging.error(f"User doesn't exist: {username}")
        sys.exit(1)
    return id


@cache
def get_all_albums(artist: str, client) -> list:
    try:
        artist_id = client._get(f"/Artists/{artist}").get("Id")
    except HTTPException as e:
        logging.error(f"Artist: {artist} error: {e}")
        return []

    search = {
        "parentId": artist_id,
        "includeItemTypes": "MusicAlbum",
        "sortBy": "ProductionYear",
        "sortOrder": "Ascending",
    }
    response = client.items(params=search)
    return response.get("Items", [])


@cache
@click.pass_context
def get_all_tracks(ctx, artist: str, album: str, client) -> dict:
    response = {}
    albums = get_all_albums(artist, client)

    for item in albums:
        if fuzz.QRatio(
            item.get("Name"),
            album,
            processor=utils.default_process,
        ) >= ctx.params.get("fuzz"):
            response = client.items(
                params={
                    "parentId": item.get("Id"),
                    "recursive": True,
                    "mediaTypes": "Audio",
                    "limit": 100,
                },
            ).get("Items", {})

    return response


def get_playlist(name: str, client, user_id: str) -> dict:
    response = client.media_folders()
    playlist_folder = {
        "ParentId": item.get("Id")
        for item in response.get("Items", {})
        if item.get("Type") == "ManualPlaylistsFolder"
    }
    response = client._get(f"Users/{user_id}/Items", params=playlist_folder)

    playlist = [
        playlist
        for playlist in response.get("Items", [])
        if playlist.get("Name") == name
    ]
    return playlist[0] if playlist else {}


@click.pass_context
def get_music(ctx, track: dict, client) -> str | None:
    if tracks := get_all_tracks(track["artistName"], track["albumName"], client):
        for item in tracks:
            if fuzz.QRatio(
                item.get("Name"),
                track["trackName"],
                processor=utils.default_process,
            ) >= ctx.params.get(
                "fuzz"
            ):  # Jellyfin can't find songs with special chars sometimes
                # fuzz.QRatio helps to solve the special chars issue
                logging.info(
                    f"Track found: {track['trackName']} Artist: {track['artistName']} Album: {track['albumName']}"
                )
                return item.get("Id")

    if ctx.params.get("any_album"):
        albums = get_all_albums(track["artistName"], client)
        for album in albums:
            for item in get_all_tracks(track["artistName"], album.get("Name"), client):
                if fuzz.QRatio(
                    item.get("Name"),
                    track["trackName"],
                    processor=utils.default_process,
                ) >= ctx.params.get("fuzz"):
                    logging.info(
                        f"Track found: {track['trackName']} Artist: {track['artistName']} Album: {item.get('Album')} AlbumOriginal: {track['albumName']}"
                    )
                    return item.get("Id")

    logging.warning(
        f"Track not found: {track['trackName']} Artist: {track['artistName']} Album: {track['albumName']}"
    )
    return None


def get_playlist_items(name: str, client, user_id: str) -> set:
    ids = set()
    if playlist := get_playlist(name, client, user_id):
        response = client._get(
            f"Playlists/{playlist.get('Id')}/Items", params={"userId": user_id}
        )
        ids = {item.get("Id") for item in response.get("Items", [])}
    return ids


@click.pass_context
def create_playlist(ctx, name: str, tracks: list, client, user):
    jellyfin_tracks = set(
        jt
        for track in tracks
        if track is not None and (jt := get_music(track, client)) is not None
    )
    if jellyfin_tracks:  # Avoid creation of empty playlists
        if playlist := get_playlist(name, client, user):
            playlist_tracks = get_playlist_items(name, client, user)
            if new_tracks := jellyfin_tracks.difference(playlist_tracks):
                logging.info(f"Playlist update: {name} added tracks: {len(new_tracks)}")
                if not ctx.params.get("dry_run"):
                    client._post(
                        f"Playlists/{playlist.get('Id')}/Items",
                        params={
                            "ids": ",".join(new_tracks),
                            "userId": user,
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
                client._post(
                    "Playlists",
                    json={
                        "Name": name,
                        "MediaType": "Audio",
                        "IsPublic": ctx.params["public"],
                        "userId": user,
                        "Ids": list(jellyfin_tracks),
                    },
                )
            else:
                logging.info(
                    f"Dry run: /Playlists | 'ids': {','.join(jellyfin_tracks)}"
                )
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


@click.pass_context
def jellyfin_client(ctx):
    client = JellyfinClient()
    client.config.data["app.name"] = "jsi"
    client.config.data["app.version"] = "0.2.0"
    client.config.data["auth.ssl"] = not ctx.params["skip_tls"]
    client.authenticate(
        {
            "Servers": [
                {"AccessToken": ctx.params["token"], "address": ctx.params["url"]}
            ]
        },
        discover=False,
    )
    return client.jellyfin


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
@click.option("--public", is_flag=True, help="Sets playlist to public on creation")
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
    public: bool,
    any_album: bool,
    url: str,
    fuzz: int,
    dry_run: bool,
    log_level: str,
    skip_tls: bool,
):
    logging.basicConfig(level=log_level.upper(), format="%(levelname)s: %(message)s")
    client = jellyfin_client()
    user_id = get_user_id(client, user)
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
                create_playlist(playlist, tracks, client, user_id)
        elif _csv:
            tracks = csv.DictReader(
                _f,
            )
            if all(
                field in (tracks.fieldnames or [])
                for field in ["trackName", "artistName", "albumName"]
            ):
                create_playlist(
                    Path(filename).stem, [row for row in tracks], client, user_id
                )
            else:
                logging.error("CSV: cannot find field(s)")
                sys.exit(1)


if __name__ == "__main__":
    main(prog_name="jsi")
