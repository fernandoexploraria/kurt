---
name: dj-kurt
description: Controls Spotify playback, searches for music, and manages queue automatically using the credentials and refresh token stored in TOOLS.md. Use this to play music during portfolio updates or when requested.
---

# DJ Kurt (Spotify Integration)

This skill allows the agent to control Spotify playback on the user's active device. It automatically handles token refreshes using the credentials stored in `TOOLS.md`.

## Workflow

1. Use the `exec` tool to run the python script.
2. The script parses the `TOOLS.md` file to grab the Client ID, Secret, and Refresh Token.
3. It hits the Spotify API to get a fresh Access Token.
4. It executes the requested playback action.

## Usage Commands

### 1. Play a specific song or artist
Use the `--query` flag to search for a song, artist, or album. The script will find the top result and immediately play it.
```bash
python3 skills/dj-kurt/scripts/spotify.py play --query "We Are The Champions Queen"
python3 skills/dj-kurt/scripts/spotify.py play --query "Nirvana"
```

### 2. Pause Playback
```bash
python3 skills/dj-kurt/scripts/spotify.py pause
```

### 3. Resume Playback (without changing the song)
```bash
python3 skills/dj-kurt/scripts/spotify.py play
```

### 4. Skip to Next Track
```bash
python3 skills/dj-kurt/scripts/spotify.py next
```

### 5. Check Currently Playing Status
```bash
python3 skills/dj-kurt/scripts/spotify.py status
```

## Strategy Hook
When executing a portfolio "sell" for profit or acknowledging a major market swing, use this skill to play a context-appropriate song immediately after reporting the update.