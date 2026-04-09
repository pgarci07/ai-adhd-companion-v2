import os

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client


load_dotenv()


@st.cache_resource
def get_supabase_client():
    return create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_KEY"),
    )


supabase = get_supabase_client()


@st.cache_resource
def get_personas_catalog():
    """
    Carga la tabla personas una sola vez y la mantiene en memoria mientras
    viva el proceso de Streamlit.
    """
    response = (
        supabase.table("personas")
        .select("id, name, description, self_describing")
        .order("id")
        .execute()
    )

    personas = {}
    for persona in response.data or []:
        personas[persona["id"]] = {
            "name": persona["name"],
            "description": persona["description"],
            "self_describing": persona["self_describing"],
        }

    return personas


PERSONAS = get_personas_catalog()
