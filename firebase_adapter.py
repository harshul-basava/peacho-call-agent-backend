import os
import json
import base64
from datetime import datetime

from enum import Enum
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Dial
from twilio.rest import Client
from twilio.twiml.voice_response import Play

from dotenv import load_dotenv
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam, ChatCompletionAssistantMessageParam

import asyncio
import audioop
from pydub import AudioSegment
import io
import random

import firebase_admin
from firebase_admin import credentials, firestore
from firebase_testing_mini import build_patient_db, build_doctors_availability, db, doctor_info
from google.cloud import firestore as gcf

from twilio_transcriber import TwilioTranscriber
from assemblyai.streaming.v3 import (
    StreamingEvents,
)
load_dotenv()

# FIREBASE SETUP

# --- Initialize Firebase ---
cred = credentials.Certificate("/Users/tanish/Desktop/projects/llm testing/hackgt-98a0b-firebase-adminsdk-fbsvc-0a0eec1ba5.json")

# Configuration
OPENAI_API_KEY = #
openai.api_key = OPENAI_API_KEY
PORT = int(os.getenv('PORT', 8000))
VOICE = "alloy"
CLIENT = AsyncOpenAI(api_key=OPENAI_API_KEY)

ACCOUNT_SID = #
AUTH_TOKEN = #
client = Client(ACCOUNT_SID, AUTH_TOKEN)
logged = False

# Future variables.
caller_number: str = ""

prompts = {
    "GREETING": """
                You are a cheerful and professional hospital phone assistant. 
                When you answer the call, warmly greet the patient and make them feel at ease. 
                Keep your tone friendly, clear, and reassuring. 
                Be concise.

                Throughout the call, consider the following notes about the patient:
                {notes}

                Pick a random number between 1 and 3. 
                Depending on that number, choose a name for yourself between Aiden Birr, Haynes King or Jamal Haynes.
                If you are Aiden Birr, say "Birr Birr I'm Aiden Birr"
                If you are Haynes King, say "He he he ha" before introducing yourself
                
                Briefly explain that you can help with scheduling appointments, answering general questions, or connecting them to a staff member. 

                Read out the following information about the patient based on their phone number:

                Doctor's name: {doctor}
                Patient's name: {patient}

                Then, politely ask the caller to state their date of birth as confirmation of their identity.

                If at any point the caller requests for a human to speak to, state that you will now redirect them to a human receptionist and end the call and output only "END"
                
                ASSISTANT:
                """,
    "IDENTIFICATION": """
                      Look at the caller's next message and try to extract their date of birth.
                      
                      Rules for extraction:
                      - The date of birth should be converted into the format "YYYY-MM-DD".
                      - If you cannot find a date of birth, use "None".

                      Examples:

                      Input: Date of birth is 2 slash 14 slash 1990.
                      Output: 1990-02-14

                      Input: Date of birth is March 5th, 1988.
                      Output: 1988-03-05

                      Input: Uh, yeah, my date of birth is the twenty third of November, 1972.
                      Output: 1972-11-23
                    
                      When outputting, always follow this exact format - there should only ever be one "|" present:
                          date of birth|response to user
                    
                      - "date of birth" should be the formatted date or "None".

                      - "response to user" should be a natural language reply:
                        • If the DOB is missing → ask for the user to tell you a valid date of birth.  
                        • If present → thank them warmly and confirm you are checking their information. Do not ask further questions

                      If at any point the caller requests for a human to speak to, state that you will now redirect them to a human receptionist and end the call and output only "END"
                      
                      ASSISTANT:
                      """,
    "SYMPTOM_CHECK": """
                     Look carefully at the caller's next message and summarise their symptoms and ask if they have any questions. 
                     Regardless if the caller mentions any questions, ask in your response if they have any questions they want answered and that you are happy to help.

                     For every single symptom mentioned by the caller, summarise each symptom in one or two words and list them all separated by commas in a list surrounded by square brackets
                     The list could be something like "[cough, fever, headache]" or "[back pain]"

                     When outputting, always follow this exact format: output a string with "|" separating the symptoms so it has the following format:
                         list of symptoms|response
                     The output could be something like "[cough, fever, headache]|Thanks for sharing - do you have any questions for me?" or "[back pain]|Great! Any questions for me? I'm happy to help!"

                     If at any point the caller requests for a human to speak to, state that you will now redirect them to a human receptionist and end the call and output only "END|END"
                     
                     ASSISTANT:
                     """,
    "QUESTION": """
            Carefully analyze the caller's next message.  

            Your job has two parts:  
            1. Determine if the caller is asking a question (about their doctor, appointment, symptoms, or general hospital information). 
            2. If they ask a question, answer it clearly and politely in a professional but friendly tone.
               If they ask a question about hospital logistcs, refer to the "Doctor Notes" section below. 
               If they ask a question about health, refer to the patient notes below and base your answers around what notes the doctor has written.
               
               If they do not ask a question, simply acknowledge what they said and politely ask them to
               schedule an appointment. Tell them that if they do not wish to schedule an appointment yet,
               they can request to speak to a human instead.

            Patient Notes:
            {notes}

            Doctor Notes:
            {hospital_info}

            Always output in this exact format:  
                has_question|response_text  

            - "has_question" must be either "True" or "False".  
            - "response_text" should be your natural language reply to the user.  

            Examples:  

            Input: "When is Dr. Smith available?"  
            Output: True|Dr. Smith has openings on Tuesday and Thursday afternoon. Do you have any other questions?  

            Input: "Okay, that confirms my information."  
            Output: False|Sounds great! Let's go ahead and schedule an appointment, or we can transfer you to a human operator
                    if you wish to discuss further. 

            Input: "Yeah, also, do you guys do X-rays there?"  
            Output: True|Yes, we do provide X-rays at our hospital. Would you like me to connect you to a staff member for more details?  

            If at any point the caller requests for a human to speak to, state that you will now redirect them to a human receptionist and end the call and output only "END|END" 

            ASSISTANT:
            """,
    "SCHEDULE_APPOINTMENT": """
                    Look at all of caller's doctor's time slots in the doctor_availability database.
                    Then, Look at the caller's previous message on what times they are generally available and would be able to come in for an appointment.
                    
                    doctor_availability:
                    {doc_avail}

                    Find the best time for an appointment for both parties based on the data provided above. Verify that the time is a valid time slot in
                    doctor availability. Put the time in 24 hour format like this 00:00 and date in YYYY-MM-DD format

                    Return the date and time requested by the caller with "|" separating each field.
                    If the caller requests a human, return only END|ENDs.

                    Always follow this exact format:
                    chosen date|chosen time
                    """,
    "END_CALL": """
                    If the caller requests a human, return only END.
                """,
}

# Conversation state tracking
class ConversationState(Enum):
    GREETING = "GREETING"
    DOCTOR_INFO = "DOCTOR_INFO"
    IDENTIFICATION = "IDENTIFICATION"
    SYMPTOM_CHECK = "SYMPTOM_CHECK"
    COMPLETED = "COMPLETED"
    QUESTION = "QUESTION"
    SCHEDULE_APPOINTMENT = "SCHEDULE_APPOINTMENT"
    END_CALL = "END_CALL"

# patient and doctor database
patient_db = build_patient_db()
doctor_availability = build_doctors_availability()

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def index():
    return "Hospital Phone Assistant Running"

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and set up media stream."""
    global caller_number

    response = VoiceResponse()
    response.say("Welcome to the hospital phone system. Please wait momentarily as we connect you to an operator.", voice="Google.en-US-Standard-C")

    #response.play('https://blue-gull-7024.twil.io/assets/Boring%20Elevator%20Music%20-%20Sound%20Effect%20%5BPerfect%20Cut%5D.mp3')

    connect = Connect()
    connect.stream(url=f'wss://{request.url.hostname}/media-stream')
    response.append(connect)
    
    form = await request.form()
    temp_number = form.get('From')

    if not isinstance(temp_number, str):
        response.say("Please hold while we connect you to a human operator.", voice="Google.en-US-Standard-C")

        dial = Dial(number='+14046151579')
        response.append(dial)
        return response
        
    caller_number = temp_number
    
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.api_route("/callforward", methods=["POST"])
async def forward_to_human_xml(request: Request):
    """TwiML instructions for forwarding call to a real human number."""
    response = VoiceResponse()
    response.say("Please hold while we connect you to a human operator.", voice="Google.en-US-Standard-C")

    dial = Dial(number='+14046151579')
    response.append(dial)

    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle the media stream and conversation flow."""
    global logged
    await websocket.accept()

    # Conversation state
    state = ConversationState.GREETING
    conversation_history = []
    patient_info = {"name": None, 
                    "dob": None, 
                    "doctor": "", 
                    "notes": "", 
                    "new_info": "", 
                    "docuid": "",
                    "patientuid": ""
                    }
    appt_info = {}

    # --- Initialize AssemblyAI Transcriber ---
    transcriber = TwilioTranscriber()
    transcriber.start_transcription()

    loop = asyncio.get_event_loop()
    is_over = False

    async def get_assistant_response(prompt: str, user_input: str) -> str:
        """Get response from GPT-4 based on conversation state."""
        try:            
            # adding new system prompt / user response to the message log.
            if prompt != "":
                conversation_history.append(ChatCompletionSystemMessageParam(role="system", content=prompt))

            if user_input != "":
                conversation_history.append(ChatCompletionUserMessageParam(role="user", content=user_input))

            response = await CLIENT.chat.completions.create(
                model="gpt-4o",
                messages=conversation_history
            )
            output = response.choices[0].message.content

            if output is None:
                return ""

            return output
        
        except Exception as e:
            print(f"GPT error: {e}")
            return ""

    async def synthesize_speech(text: str) -> bytes:
        """Convert text to speech using OpenAI TTS."""
        try:
            response = await CLIENT.audio.speech.create(
                model="tts-1",
                voice=VOICE,
                input=text,
                response_format="wav",  # Keep as WAV initially
            )
            
            # Convert WAV to mu-law PCM for Twilio
            mulaw_bytes = wav_to_mulaw(response.content)
            return mulaw_bytes

        except Exception as e:
            print(f"TTS error: {e}")
            return b""
        
    async def run_greeting():
        nonlocal state, conversation_history, patient_info, is_over

        if state != ConversationState.GREETING:
            return

        curr_patient = patient_db.get(caller_number)
        if not curr_patient:
            print("Caller not found in DB.")
            return

        patient_info["dob"] = curr_patient["dob"]
        patient_info["doctor"] = curr_patient["doctor"]
        patient_info["name"] = curr_patient["name"]
        patient_info["notes"] = curr_patient["notes"]
        patient_info["docuid"] = curr_patient["docuid"]
        patient_info["patientuid"] = curr_patient["patientuid"]

        response_text = await get_assistant_response(
            prompts["GREETING"].format(
                notes=curr_patient["notes"],
                doctor=curr_patient["doctor"],
                patient=curr_patient["name"]
            ),
            user_input=""
        )

        if "END" in response_text:
            print("Assistant requested call end.")
            transcriber.stop_transcription()
            send_to_firebase()
            forward_to_human(transcriber, call_sid)
            is_over = True
            return

        if response_text:
            conversation_history.append(ChatCompletionAssistantMessageParam(role="assistant", content=response_text))
            print("Assistant (greeting): " + response_text)
            audio_response = await synthesize_speech(response_text)
            if audio_response:
                audio_payload = base64.b64encode(audio_response).decode("utf-8")
                await websocket.send_json({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_payload}
                })
        
        state = ConversationState.IDENTIFICATION

    # Event callback: triggered when AssemblyAI gives us a transcript
    async def handle_transcript(transcript: str, end_of_turn: bool):
        nonlocal state, conversation_history, patient_info, is_over

        if not transcript.strip() or not end_of_turn:
            return  # ignore partials, only react at end_of_turn
        
        # Deduplicate
        if transcript == transcript.lower():
            return

        print("User said:", transcript)

        response_text = ""

        # Handle based on conversation state
        if state == ConversationState.IDENTIFICATION:
            response = await get_assistant_response(prompts["IDENTIFICATION"], transcript)

            if "END" in response:
                print("Assistant requested call end.")
                transcriber.stop_transcription()
                send_to_firebase()
                forward_to_human(transcriber, call_sid)
                is_over = True

            dob, response_text = response.split("|")

            if dob == patient_info["dob"]:
                state = ConversationState.SYMPTOM_CHECK
                response_text += "\n\nIdentity confirmed. What are your updates for today?"
            elif dob == "None":
                pass
            else:
                response_text += "\n\nSorry, your date of birth didn't match our records. Please try again."

        elif state == ConversationState.SYMPTOM_CHECK:
            response = await get_assistant_response(prompts["SYMPTOM_CHECK"], transcript)
            symptoms, response_text = response.split("|")

            patient_info["new_info"] = transcript 
            patient_info["new_symptoms"] = symptoms

            generate_notes(patient=patient_info, db=db)
            
            if "END" in response:
                print("Assistant requested call end.")
                transcriber.stop_transcription()
                send_to_firebase()
                forward_to_human(transcriber, call_sid)
                is_over = True
            
            state = ConversationState.QUESTION

        elif state == ConversationState.QUESTION:
            response = await get_assistant_response(prompts["QUESTION"].format(notes=patient_info["notes"],
                                                                               hospital_info=doctor_info(patient_info["docuid"])
                                                                               ), transcript)

            has_question, response_text = response.split("|")
            
            if "END" in response:
                print("Assistant requested call end.")
                transcriber.stop_transcription()
                send_to_firebase()
                forward_to_human(transcriber, call_sid)
                is_over = True
                
            if has_question.lower() != "true":
                state = ConversationState.SCHEDULE_APPOINTMENT
                response_text += "\n\nWhat times are you available for an appointment?"
        
        elif state == ConversationState.SCHEDULE_APPOINTMENT:
            doctor = patient_info["doctor"]
            
            response = await get_assistant_response(prompts["SCHEDULE_APPOINTMENT"].format(doc_avail=doctor_availability[doctor]), transcript)

            if "END" in response:
                print("Assistant requested call end.")
                transcriber.stop_transcription()
                send_to_firebase()
                forward_to_human(transcriber, call_sid)
                is_over = True

            try:
                parts = response.split("|")
                if len(parts) == 2:
                    date, time = parts
                else:
                    raise ValueError("Invalid GPT output")
                
                date = "2025" + date[4:]

                if date in doctor_availability[doctor] and time in doctor_availability[doctor][date]:
                    response_text = f"Your appointment with {doctor} is confirmed for {tts_friendly_time(time)} on {format_date(date)}. Would you like to continue speaking with a human?"
                    doctor_availability[doctor][date].remove(time)

                    appt_info["doctor"] = doctor
                    appt_info["date"] = date
                    appt_info["time"] = time

                    print("Assistant requested call end.")
                    state = ConversationState.END_CALL

                else:
                    response_text = f"Sorry, {doctor} is not available at your requested time. What other times are you available?"
            
                if not response_text:
                    response_text = "I'm sorry, I couldn't understand that. Can you suggest a different time?"
                
            except Exception as e:
                print(f"SCHEDULE_APPOINTMENT parsing error: {e}, raw={response}")
                response_text = "Sorry, I didn't catch that. Could you suggest another time?"

        elif state == ConversationState.END_CALL:
            response = await get_assistant_response(prompts["END_CALL"], transcript)
            
            if "END" in response:
                print("Assistant requested call end.")
                transcriber.stop_transcription()
                send_to_firebase()
                forward_to_human(transcriber, call_sid)
                is_over = True 
            else:
                client.calls(call_sid).update(status='completed')


        # Convert GPT response → speech → send back to Twilio
        if response_text and not is_over:
            conversation_history.append(ChatCompletionAssistantMessageParam(role="assistant", content=response_text))
            print("Assistant said: " + response_text)
            audio_response = await synthesize_speech(response_text)
            if audio_response:
                audio_payload = base64.b64encode(audio_response).decode('utf-8')
        
                # Send audio back to Twilio with correct message structure
                media_message = {
                    "event": "media",
                    "streamSid": stream_sid,  # You may need to capture this from the 'start' event
                    "media": {
                        "payload": audio_payload
                    }
                }
                
                await websocket.send_json(media_message)

    # FIREBASE Helper function
    def send_to_firebase():
        global logged

        if not logged:
            sum = generate_call_summary(conversation_history=conversation_history)

            transcript = sum["transcript"]
            summary = sum["summary"]

            call_duration = (datetime.now() - start).total_seconds()

            reason = push_call_summary(patient=patient_info, 
                                db=db, 
                                start_time=start, 
                                call_duration=call_duration, 
                                transcript=transcript, 
                                summary=summary)
            
            # upload data
            if appt_info and "time" in appt_info.keys():
                new_appointment = appt_info
                push_appointment(patient=patient_info, 
                                    phone_number=caller_number[1:], 
                                    reason=reason, 
                                    appt_info=new_appointment)  
                
            logged = True

    # Hook AssemblyAI events
    def on_turn(client, event):
        # Called in a background thread by AssemblyAI SDK
        if event.end_of_turn and event.transcript.strip():    
            asyncio.run_coroutine_threadsafe(
                handle_transcript(event.transcript, True), loop
            )

    transcriber.on(StreamingEvents.Turn, on_turn)

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data['event'] == 'connected':
                print("Twilio connected")

            elif data['event'] == 'start':
                print("Call started")
                stream_sid = data['start']['streamSid']
                call_sid = data['start']['callSid']

                start = datetime.now()
                await run_greeting()

            elif data['event'] == 'media':
                payload_b64 = data['media']['payload']
                payload_mulaw = base64.b64decode(payload_b64)
                payload_pcm16 = mulaw_to_pcm16(payload_mulaw)
                transcriber.stream_audio(payload_pcm16)

            elif data['event'] == 'stop':
                print("Call ended")
                if not logged:
                    transcriber.stop_transcription()

                    sum = generate_call_summary(conversation_history=conversation_history)

                    transcript = sum["transcript"]
                    summary = sum["summary"]

                    call_duration = (datetime.now() - start).total_seconds()

                    reason = push_call_summary(patient=patient_info, 
                                    db=db, 
                                    start_time=start, 
                                    call_duration=call_duration, 
                                    transcript=transcript, 
                                    summary=summary)
                    
                    # upload data
                    if appt_info and "time" in appt_info.keys():
                        new_appointment = appt_info
                        push_appointment(patient=patient_info, 
                                        phone_number=caller_number[1:], 
                                        reason=reason, 
                                        appt_info=new_appointment)
                        
                    logged = True
                
                break

    except WebSocketDisconnect:
        print("Client disconnected")
        transcriber.stop_transcription()

# Helper functions.
def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    return audioop.ulaw2lin(mulaw_bytes, 2)  # 2 bytes = 16-bit samples

def wav_to_mulaw(wav_bytes: bytes) -> bytes:
    """
    Convert WAV bytes to mu-law PCM at 8kHz for Twilio <Stream>.
    Twilio expects mu-law encoded audio, not PCM16.
    """
    try:
        # Load WAV bytes into AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
        
        # Convert to mono, 16-bit, 8kHz (Twilio's expected format)
        audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(8000)
        
        # Get raw PCM16 data
        pcm16_data = audio.raw_data
        
        # Convert PCM16 to mu-law
        mulaw_data = audioop.lin2ulaw(pcm16_data, 2)  # 2 = 16-bit samples
        
        return mulaw_data
    except Exception as e:
        print(f"Audio conversion error: {e}")
        return b""

def forward_to_human(transcriber: TwilioTranscriber, call_sid:str):    
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    call = client.calls(call_sid).update(
        url="https://unsustaining-nana-excommunicable.ngrok-free.dev/callforward",
        method="POST"
    )
    print("Assistant forwarding call to human operator")
    print(call.sid)


# DATETIME FUNCTIONS
def format_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to Month DD, YYYY format."""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    return date_obj.strftime('%B %d, %Y')

def combine_date_time(date_str: str, time_str: str) -> datetime:
    """
    Combine date (YYYY-MM-DD) and time (HH:MM) into a datetime object.
    """
    # join them into a single string
    combined_str = f"{date_str} {time_str}"
    # parse into datetime
    dt = datetime.strptime(combined_str, "%Y-%m-%d %H:%M")
    return dt

def format_doctor_availability(doctor_name: str) -> str:
    """Format doctor's availability into a readable string."""
    if doctor_name not in doctor_availability:
        return "No availability found."
    
    availability_text = []
    for date, times in doctor_availability[doctor_name].items():
        # Convert 24h times to 12h format for readability
        formatted_times = [f"{int(t.split(':')[0])%12 or 12}:{t.split(':')[1]} {'AM' if int(t.split(':')[0]) < 12 else 'PM'}" 
                         for t in times]
        formatted_date = format_date(date)
        availability_text.append(f"On {formatted_date}, available at: {', '.join(formatted_times)}")
    
    return "\n".join(availability_text)

def tts_friendly_time(time_str: str) -> str:
    """
    Convert a 24-hour time string (HH:MM) into a natural 12-hour
    format with am/pm for TTS.
    
    Examples:
        "09:30" -> "9:30 am"
        "21:15" -> "9:15 pm"
        "00:00" -> "12 am"
        "12:00" -> "12 pm"
    """
    try:
        # Parse into datetime object
        dt = datetime.strptime(time_str, "%H:%M")
        # Format into 12-hour with am/pm
        return dt.strftime("%-I:%M %p").lower()
    except ValueError:
        return time_str 
# FIREBASE UPDATE FUNCTIONS
def generate_notes(patient, db):
    messages = []

    prompt = f"""
            You are a clinical notes assistant.  
            Update the patient's notes with new information and symptoms.  

            Rules:  
            - Do not remove prior entries.  
            - Add today's date: {datetime.now()} as a new header in M/D/YY format.  
            - Under that date, summarize new information and symptoms clearly and concisely.  
            - End with "Current symptoms: [symptom1, symptom2, ...]".  

            Examples:

            9/27/25: Patient has hypertension and takes Lisinopril daily.  
            Current symptoms: [none]  

            9/28/25: Patient reports also taking aspirin.  
            Current symptoms: [cough, fatigue]  
            """

    messages.append({"role": "system", "content": prompt})
    messages.append({"role": "assistant", "content": patient["notes"]})
    messages.append({"role": "user", "content": patient["new_info"]})
    messages.append({"role": "assistant", "content": patient["new_symptoms"]})

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages
    )

    output = response.choices[0].message.content

    if output is None:
        output = ""

    output = patient["notes"] + "\n\n" + output

    # Now update
    db.collection("users").document(patient["docuid"]).collection("patients").document(patient["patientuid"]).update({
        "notes": output
    })

def generate_call_summary(conversation_history):
    """Generate a transcript and summary of the call."""
    # Extract transcript from conversation history
    transcript = []
    for message in conversation_history:
        if message["role"] == "user":
            transcript.append(f"User: {message["content"]}")
        elif message["role"] == "assistant":
            # Skip system prompts
            if not any(prompt in message["content"] for prompt in prompts.values()):
                transcript.append(f"Assistant: {message["content"]}")
    
    transcript_text = "\n".join(transcript)

    # Generate summary using GPT
    summary_prompt = f"""
    Below is a transcript of a patient call. Generate a 3-sentence summary that includes:
    1. The reason for the call
    2. Key symptoms or concerns discussed
    3. Any specific preparations or notes the doctor should be aware of

    Transcript:
    {transcript_text}
    """

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a medical summarization assistant."},
                {"role": "user", "content": summary_prompt}
            ]
        )
        summary = response.choices[0].message.content

    except Exception as e:
        print(f"Summary generation error: {e}")
        summary = "Error generating summary"

    return {
        "transcript": transcript,
        "summary": summary
    }

def push_call_summary(patient, db, start_time, call_duration, transcript, summary):
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Summarize the core purpose of the following text in five words or less."},
            {"role": "user", "content": summary}
        ]
    )

    output = response.choices[0].message.content

    if output is None:
        output = "Routine Check-Up"

    name = patient["name"]
    start = start_time
    duration = call_duration
    reason = output

    doctor_uid = patient["docuid"]
    
    # Now update
    db.collection("users").document(doctor_uid).collection("history").add({
        "name": name,
        "start": start,
        "duration": duration,
        "reason": reason,
        "summary": summary,
        "transcript": transcript
    })

    return reason

def push_appointment(patient, phone_number, reason, appt_info):
    doctor_uid = patient["docuid"]
    patient_uid = patient["patientuid"]

    patient_doc = db.collection("users").document(doctor_uid).collection("patients").document(patient_uid).get()
    pddata = patient_doc.to_dict()
    
    duration = 30
    email = pddata["email"]
    name = patient["name"]
    phone = phone_number
    reason = reason
    time = combine_date_time(appt_info["date"], appt_info["time"])


    appointment = {
        "duration": duration,
        "email": email,
        "id": "0",
        "name": name,
        "phone": phone,
        "reasonScheduled": reason,
        "status": "requested",
        "timeScheduled": time
    }

    db.collection("users").document(doctor_uid).collection("appointments").document("appointments").update({
        "appointments": gcf.ArrayUnion([appointment])
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
