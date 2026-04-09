import os
import streamlit as st
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime
import pytz # Recomendado para manejo de zonas horarias

load_dotenv()

@st.cache_resource
def get_supabase_client():
    return create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_KEY")
    )

supabase = get_supabase_client()

# Inicializo el user id en session state para evitar errores
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AI-ADHD", layout="centered")

# función para convertir un string a datetime, lo he incluido como una forma
# sencilla de convertir cosas como "1997" a algo insertable en Supabase TIMESTAMPTZ
# pero si Streamlit tiene algun control que ya se convierta una caja st.text_input
# en un date picker pues no se necesitaria
def to_supabase_timestamp(year_str):
    # Convertimos el string "1969" a un objeto datetime (1 de Enero a las 00:00)
    # y le asignamos la zona horaria UTC
    dt = datetime.strptime(year_str, "%Y").replace(tzinfo=pytz.UTC)    
    # Retornamos el formato ISO 8601 que Supabase ama
    return dt.isoformat()


# --- FORMULARIO DE REGISTRO (Pop-up) ---
@st.dialog("Crear nueva cuenta")
def registration_form():
    with st.form("signup_form"):
        full_name = st.text_input("Nombre completo") # Dato para tu tabla 'profiles'
        email = st.text_input("Email")
        password = st.text_input("Contraseña", type="password")
	    #
        # TO-DO: Meter aqui en el form los controles de los campos del usuario que necesitamos
        born = st.text_input("Birth date")
        persona_id = 1  # "Alex". TO-DO: identificar que perfil ADHD se es
	    #
        # --- EN TU FORMULARIO DE REGISTRO ---
        if st.form_submit_button("Registrarse"):
            try:
                # 1. Crear usuario en Auth
                auth_res = supabase.auth.sign_up({"email": email, "password": password})
                user_id = auth_res.user.id
                
                if user_id:
                    # GUARDAR EN SESSION STATE PARA CONSUMO EN TODAS LAS PAGINAS Y FORMULARIOS DE LA APLICACION
                    st.session_state["user_id"] = user_id
                    
                    # TO-DO: Construir el campo preferencias cuando se sepa qué y cómo incluir en unas semanas
                    preferences = {
                        "languaje": "EN",
                        "time-mgmt": "Pomodoro",
                        "sprint": 30,
                        "notifications": True
                    }
                    born_date = to_supabase_timestamp(born)

                    # 2. Insertar en la tabla 'profiles' de tu DDL
                    profile_res = supabase.table("profiles").insert({
                        "id": user_id, 
                        "full_name": full_name,
                        # TO-DO: los siguientes campos de profiles hay que capturarlos del form de registro
                        "born": born_date,
                        "preferences": preferences,
                        "persona_id": persona_id,
                        "state_id": None                
                    }).execute()
                    
                    st.success("Registro completado y ID guardado en sesión. Mira en tu buzón para completar el registro.")
          
            except Exception as e:
                st.error(f"Error en el registro: {e}")

# --- FORMULARIO DE LOGIN (Pop-up) ---
@st.dialog("Iniciar Sesión")
def login_form():
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Contraseña", type="password")
        
        if st.form_submit_button("Entrar"):
            try:
                # Autenticar con Supabase
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                
                # Guardar el ID en el session_state
                st.session_state["user_id"] = res.user.id
                st.success("¡Bienvenido de nuevo!")
                st.rerun()  # Recargamos para actualizar la interfaz
                
            except Exception as e:
                st.error("Email o contraseña incorrectos")

# --- LÓGICA DE CIERRE DE SESIÓN ---
def logout():
    supabase.auth.sign_out()
    st.session_state["user_id"] = None
    st.rerun()


# Verificamos si hay sesión activa al cargar
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

# --- FLUJO PRINCIPAL ---
if st.session_state["user_id"]:
    # ESTO ES LO QUE VE EL USUARIO LOGUEADO
    st.sidebar.write(f"Usuario: {st.session_state['user_id']}")
    if st.sidebar.button("Cerrar Sesión"):
        logout()
    
    st.title("Panel de Control AI-ADHD")
    st.write("Aquí irá el resto de tu prototipo.")
    # Aquí puedes llamar a otras funciones de tus carpetas app/application/ o app/domain/

else:
    # ESTO ES LA LANDING PAGE
    st.title("Bienvenido a AI-ADHD")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Crear Cuenta", type="primary", use_container_width=True):
            registration_form()
    with col2:
        if st.button("Iniciar Sesión", use_container_width=True):
            login_form()
