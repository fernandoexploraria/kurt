import os
import sys
import re
import base64
import json
import urllib.request
import urllib.parse
import argparse

TOOLS_PATH = os.path.expanduser("~/.openclaw/workspace/TOOLS.md")

def get_credentials():
    creds = {
        'client_id': os.environ.get("SPOTIFY_CLIENT_ID"),
        'client_secret': os.environ.get("SPOTIFY_CLIENT_SECRET"),
        'refresh_token': os.environ.get("SPOTIFY_REFRESH_TOKEN")
    }
    
    if not all(creds.values()):
        print("Error: Missing Spotify credentials in environment variables.")
        sys.exit(1)
    return creds

def get_access_token(creds):
    url = "https://accounts.spotify.com/api/token"
    auth_str = f"{creds['client_id']}:{creds['client_secret']}"
    b64_auth_str = base64.b64encode(auth_str.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {b64_auth_str}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": creds['refresh_token']
    }).encode()
    
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read())
            return res_data.get('access_token')
    except Exception as e:
        print(f"Failed to get access token: {e}")
        sys.exit(1)

def api_request(endpoint, token, method='GET', payload=None):
    url = f"https://api.spotify.com/v1/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as response:
            content = response.read()
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return {}
            return {}
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"API Error ({e.code}): {err}")
        sys.exit(1)

def search_track(query, token):
    encoded_query = urllib.parse.quote(query)
    res = api_request(f"search?q={encoded_query}&type=track,artist&limit=1", token)
    
    tracks = res.get('tracks', {}).get('items', [])
    if tracks:
        return tracks[0]['uri'], f"{tracks[0]['name']} by {tracks[0]['artists'][0]['name']}"
    
    artists = res.get('artists', {}).get('items', [])
    if artists:
        return artists[0]['uri'], f"Artist: {artists[0]['name']}"
        
    print(f"Could not find anything for: {query}")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="DJ Kurt - Spotify Controller")
    parser.add_argument("action", choices=['play', 'pause', 'next', 'status', 'queue'], help="Action to perform")
    parser.add_argument("--query", "-q", help="Song or artist to search and play", type=str)
    
    args = parser.parse_args()
    
    creds = get_credentials()
    token = get_access_token(creds)
    
    if args.action == 'play':
        if args.query:
            uri, name = search_track(args.query, token)
            payload = {"context_uri": uri} if "artist" in uri or "album" in uri else {"uris": [uri]}
            api_request("me/player/play", token, method="PUT", payload=payload)
            print(f"▶️ Playing: {name}")
        else:
            api_request("me/player/play", token, method="PUT")
            print("▶️ Resumed playback")
            
    elif args.action == 'pause':
        api_request("me/player/pause", token, method="PUT")
        print("⏸️ Paused playback")
        
    elif args.action == 'next':
        api_request("me/player/next", token, method="POST")
        print("⏭️ Skipped to next track")
        
    elif args.action == 'status':
        res = api_request("me/player/currently-playing", token)
        if res and res.get('is_playing'):
            track = res.get('item', {})
            name = track.get('name', 'Unknown')
            artist = track.get('artists', [{}])[0].get('name', 'Unknown')
            print(f"🎶 Currently Playing: {name} by {artist}")
        else:
            print("🔇 Nothing is playing right now.")
            
    elif args.action == 'queue':
        if args.query:
            uri, name = search_track(args.query, token)
            if "track" in uri:
                api_request(f"me/player/queue?uri={uri}", token, method="POST")
                print(f"📥 Added to queue: {name}")
            else:
                print("Error: Can only queue specific tracks.")
        else:
            print("Error: Please provide a song to queue using --query")

if __name__ == "__main__":
    main()
