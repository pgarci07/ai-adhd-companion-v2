"""Cached access to Supabase personas and the shared Supabase client."""

import os

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client


load_dotenv()


@st.cache_resource
def get_supabase_client():
    """Create and cache the shared Supabase client for the whole app."""

    return create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_KEY"),
    )


# Shared Supabase client reused by the UI and application-layer helpers.
supabase = get_supabase_client()


@st.cache_resource
def get_personas_catalog():
    """Load the personas table once and keep it cached for the Streamlit process."""
    response = (
        supabase.table("personas")
        .select("id, name, description, self_describing, decompose_threshold")
        .order("id")
        .execute()
    )

    personas = {}
    for persona in response.data or []:
        personas[persona["id"]] = {
            "name": persona["name"],
            "description": persona["description"],
            "self_describing": persona["self_describing"],
            "decompose_threshold": persona.get("decompose_threshold"),
        }

    return personas


# In-memory personas catalogue keyed by persona id.
PERSONAS = get_personas_catalog()
