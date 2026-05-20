"""Leads pipeline: post-call extraction of real leads into a per-tenant XLSX.

Wired from main.py after the ElevenLabs sync writes the session JSON.
Spam calls are filtered out; real leads get a row + downloaded MP3 + HTML transcript.
"""
