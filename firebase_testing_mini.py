import firebase_admin
from firebase_admin import credentials, firestore
from dateutil import parser
import random


# Initialize Firebase
cred = credentials.Certificate("/Users/tanish/Desktop/projects/llm testing/hackgt-98a0b-firebase-adminsdk-fbsvc-0a0eec1ba5.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

def build_patient_db():
    patient_db = {}

    # Get all patients (keys = phone numbers)
    patients_ref = db.collection("patients")
    patients = patients_ref.stream()

    for patient in patients:
        pdata = patient.to_dict()

        # Phone is the document ID
        phone = patient.id
        if not phone.startswith("+"):
            phone = "+1" + phone

        doctor_uid = pdata.get("doctorUID")
        patient_uid = pdata.get("patientUID")
        doctor_name = None
        patient_info = {}

        if doctor_uid and patient_uid:
            # Get doctor info
            doctor_doc = db.collection("users").document(doctor_uid).get()
            if doctor_doc.exists:
                ddata = doctor_doc.to_dict()
                if ddata:
                    doctor_name = ddata.get("name")
                    if doctor_name and not doctor_name.startswith("Dr."):
                        doctor_name = f"Dr. {doctor_name}"

            # Get patient info from doctor’s subcollection
            patient_doc = (
                db.collection("users")
                .document(doctor_uid)
                .collection("patients")
                .document(patient_uid)
                .get()
            )
            if patient_doc.exists:
                patient_info = patient_doc.to_dict()

        # Assemble dict for this patient
        patient_db[phone] = {
            "dob": patient_info.get("dateOfBirth"),
            "notes": patient_info.get("notes"),
            "name": patient_info.get("name"),
            "doctor": doctor_name,
            "docuid": doctor_uid,
            "patientuid": patient_uid,
            "email": patient_info.get("email")
        }

    return patient_db

potential_times = [
    '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
    '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
    '15:00', '15:30', '16:00', '16:30'
]

potential_days = ["2025-09-28", "2025-09-29", "2025-09-30"]

def get_user_appointments(db, user_uid, include_cancelled=False):
    """
    Traverse Firestore users -> {user_uid} -> appointments -> appointment doc,
    and extract valid (day, time) tuples from Firestore timestamp fields.
    """
    results = []

    # Go to the `appointments` subcollection of this user
    appointments_ref = db.collection("users").document(user_uid).collection("appointments")
    docs = appointments_ref.stream()

    for doc in docs:
        data = doc.to_dict()
        if not data:
            continue

        for appt in data.get("appointments", []):

            status = appt.get("status", "").lower()
            if not include_cancelled and status == "cancelled":
                continue

            ts = appt["timeScheduled"]  # This is already a datetime.datetime object

            if not ts:
                continue

            # Convert timestamp → formatted day + time
            day_str = ts.strftime("%Y-%m-%d")
            time_str = ts.strftime("%H:%M")


            if day_str in potential_days and time_str in potential_times:
                results.append((day_str, time_str))

    return results
def generate_available_slots(db, user_uid, potential_days, potential_times):
    """
    Returns a dict of all available appointment slots per day.
    """
    booked_slots = get_user_appointments(db, user_uid)
    available_schedule = {}

    for day in potential_days:
        # Filter out times already booked for this day
        available_times = [t for t in potential_times if (day, t) not in booked_slots]
        # Store all available times for this day
        available_schedule[day] = sorted(available_times)

    return available_schedule

def build_doctors_availability(db=db, sample_size=3):
    """
    For every doctor in the 'users' collection, generate available appointment slots.
    Returns a dict keyed by doctor name.
    """
    doctors_schedule = {}

    potential_times = [
    '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
    '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
    '15:00', '15:30', '16:00', '16:30'
]

    potential_days = ["2025-09-28", "2025-09-29", "2025-09-30"]

    # Get all doctors (users collection)
    doctors_ref = db.collection("users")
    doctors = doctors_ref.stream()

    for doctor in doctors:
        ddata = doctor.to_dict()
        if not ddata:
            continue

        doctor_uid = ddata.get("uid") or doctor.id
        doctor_name = ddata.get("name", "Unknown Doctor")
        if not doctor_name.startswith("Dr."):
            doctor_name = f"Dr. {doctor_name}"

        # Generate availability dict for this doctor
        availability = generate_available_slots(
            db, doctor_uid, potential_days, potential_times
        )

        doctors_schedule[doctor_name] = availability

    return doctors_schedule

def doctor_info(doctoruid, db=db) -> str:
    doctor = db.collection("users").document(doctoruid).get()
    hospital_info = doctor.to_dict()

    return str(hospital_info)



if __name__ == "__main__":
    user_uid = "GQebH4Q8Agh9DJ2CIKluBddVXt33"
    valid_appointments = build_doctors_availability(db)
    print(valid_appointments)
