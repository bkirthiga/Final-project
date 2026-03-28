import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime, timedelta
import time
import io

# MongoDB connection
MONGO_URI = st.secrets["MONGO_URI"]

@st.cache_resource
def init_connection():
    return MongoClient(MONGO_URI)

client = init_connection()
db = client["APOAI"]
faculty_collection = db["faculty"]
subjects_collection = db["subjects"]
timetable_collection = db["timetable"]
duties_collection = db["duties"]

# Initialize session state
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'current_user' not in st.session_state:
    st.session_state.current_user = None
if 'is_admin' not in st.session_state:
    st.session_state.is_admin = False

# Auto-delete past duties and timetables every 10 seconds
def cleanup_past_data():
    """Delete duties and timetables with past dates"""
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Delete past duties
    duties_deleted = duties_collection.delete_many({"date": {"$lt": current_date}})
    
    # Delete past timetable entries (if they have a date field)
    # Note: Timetables are recurring, but if you want to delete them based on some criteria
    # For now, we'll delete duties only as timetables don't have dates
    # If you add date fields to timetables, uncomment below:
    # timetable_deleted = timetable_collection.delete_many({"date": {"$lt": current_date}})
    
    return duties_deleted.deleted_count

# Run cleanup in background (checking every time page loads)
if 'last_cleanup' not in st.session_state:
    st.session_state.last_cleanup = datetime.now()

# Check every 10 seconds
if (datetime.now() - st.session_state.last_cleanup).total_seconds() > 10:
    deleted_count = cleanup_past_data()
    st.session_state.last_cleanup = datetime.now()
    if deleted_count > 0:
        st.rerun()  # Refresh to show updated data

# Login function
def login(username, password):
    faculty = faculty_collection.find_one({"username": username})
    if faculty and faculty.get("password") == password:
        st.session_state.logged_in = True
        st.session_state.current_user = faculty
        st.session_state.is_admin = faculty.get("admin", False)
        return True
    return False

# Logout function
def logout():
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.is_admin = False

# Helper function to get only staff members (non-admin)
def get_staff_only():
    return list(faculty_collection.find({"admin": {"$ne": True}}))

# Helper function to get only admin members
def get_admin_only():
    return list(faculty_collection.find({"admin": True}))

# Helper function to check time slot conflicts
def check_time_conflict(faculty_id, time_slot, days):
    """Check if faculty has conflicting time slots"""
    for day in days:
        existing = timetable_collection.find_one({
            "faculty_id": faculty_id,
            "time_slot": time_slot,
            "days": day
        })
        if existing:
            return True
    return False

# Helper function to check duplicate duties
def check_duplicate_duty(faculty_id, duty_type, date):
    """Check if faculty already has this duty type on this date"""
    existing = duties_collection.find_one({
        "faculty_id": faculty_id,
        "duty_type": duty_type,
        "date": date
    })
    return existing is not None

# Helper function to calculate burnout index
def calculate_burnout_index(faculty_id):
    """Calculate Faculty Burnout Index (0-100) - Only for staff"""
    faculty = faculty_collection.find_one({"_id": faculty_id})
    if not faculty or faculty.get("admin", False):
        return 0
    
    classes = list(timetable_collection.find({"faculty_id": faculty_id}))
    duties = list(duties_collection.find({"faculty_id": faculty_id}))
    
    teaching_hours = sum(c.get("hours_per_week", 0) for c in classes)
    duty_hours = sum(d.get("hours", 0) for d in duties)
    total_hours = teaching_hours + duty_hours
    
    burnout = min(total_hours * 2.5, 100)
    return int(burnout)

def get_expertise_match(faculty_id, subject):
    """Get expertise match percentage"""
    faculty = faculty_collection.find_one({"_id": faculty_id})
    if not faculty or faculty.get("admin", False):
        return 0
    
    expertise = faculty.get("expertise", [])
    if subject.lower() in [e.lower() for e in expertise]:
        return 95
    return 30

# Export timetable to CSV
def export_timetable_to_csv():
    """Export complete timetable to CSV"""
    timetable_data = list(timetable_collection.find())
    if not timetable_data:
        return None
    
    export_data = []
    for item in timetable_data:
        faculty = faculty_collection.find_one({"_id": item["faculty_id"]})
        export_data.append({
            "Staff ID": item["faculty_id"],
            "Staff Name": item["faculty_name"],
            "Subject Code": item["subject_code"],
            "Subject": item["subject"],
            "Time Slot": item["time_slot"],
            "Days": ", ".join(item["days"]),
            "Hours per Week": item["hours_per_week"]
        })
    
    df = pd.DataFrame(export_data)
    return df

# Login Page
if not st.session_state.logged_in:
    st.title("Faculty Login System")
    
    user_count = faculty_collection.count_documents({})
    
    if user_count == 0:
        st.info("Welcome! No users found. Please create the first admin user.")
        
        with st.form("create_first_admin"):
            st.subheader("Create First Admin User")
            name = st.text_input("Full Name")
            username = st.text_input("Username")
            department = st.text_input("Department")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            if st.form_submit_button("Create Admin User", type="primary"):
                if not all([name, username, password, confirm_password]):
                    st.error("Please fill all fields!")
                elif password != confirm_password:
                    st.error("Passwords don't match!")
                elif len(password) < 4:
                    st.error("Password must be at least 4 characters!")
                elif " " in username:
                    st.error("Username cannot contain spaces!")
                else:
                    admin_data = {
                        "_id": username.lower(),
                        "name": name,
                        "username": username.lower(),
                        "department": department,
                        "expertise": [],
                        "max_hours": 0,
                        "admin": True,
                        "password": password,
                        "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    try:
                        faculty_collection.insert_one(admin_data)
                        st.success("Admin user created successfully! Please login now.")
                        time.sleep(2)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error creating user: {str(e)}")
    else:
        with st.form("login_form"):
            st.subheader("Login")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            
            col1, col2 = st.columns([3, 1])
            with col1:
                login_btn = st.form_submit_button("Login", type="primary", use_container_width=True)
            with col2:
                clear_btn = st.form_submit_button("Clear", type="secondary", use_container_width=True)
            
            if login_btn:
                if not username or not password:
                    st.error("Please enter both username and password!")
                else:
                    if login(username.lower(), password):
                        st.success("Welcome back!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Invalid username or password!")
        
        admin_count = faculty_collection.count_documents({"admin": True})
        staff_count = faculty_collection.count_documents({"admin": {"$ne": True}})
        st.info(f"Admins: {admin_count} | Staff: {staff_count}")
    
    st.stop()

# Main Application
if st.session_state.is_admin:
    st.title("Admin Management System")
    user_role = "Administrator"
else:
    st.title("Staff Portal")
    user_role = "Staff Member"

col1, col2 = st.columns([3, 1])
with col1:
    st.write(f"*Welcome:* {st.session_state.current_user['name']} (ID: {st.session_state.current_user['_id']}) ({user_role})")
with col2:
    if st.button("Logout"):
        logout()
        st.rerun()

st.markdown("---")

# Navigation
if st.session_state.is_admin:
    page_options = [
        "Admin Dashboard", 
        "Automatic Timetable",
        "Staff Workload Balance",
        "Expertise Matching",
        "Staff Burnout Monitor",
        "Non-teaching Duties",
        "System Reports"
    ]
    st.sidebar.info("Administrator Access")
else:
    page_options = [
        "My Dashboard",
        "My Burnout Index",
        "My Reports"
    ]
    st.sidebar.info("Staff Member Access")

page = st.sidebar.selectbox("Choose Section", page_options)

# ============ ADMIN PAGES ============
if st.session_state.is_admin:
    
    if page == "Admin Dashboard":
        st.header("Admin Management Dashboard")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            staff_count = faculty_collection.count_documents({"admin": {"$ne": True}})
            st.metric("Staff Members", staff_count)
        with col2:
            admin_count = faculty_collection.count_documents({"admin": True})
            st.metric("Administrators", admin_count)
        with col3:
            subjects_count = subjects_collection.count_documents({})
            st.metric("Subjects", subjects_count)
        with col4:
            scheduled_count = timetable_collection.count_documents({})
            st.metric("Scheduled", scheduled_count)
        
        tab1, tab2, tab3 = st.tabs(["Manage Staff", "Manage Admins", "Manage Subjects"])
        
        with tab1:
            st.subheader("Add New Staff Member")
            with st.form("add_staff"):
                col1, col2 = st.columns(2)
                with col1:
                    name = st.text_input("Full Name")
                    username = st.text_input("Username")
                    department = st.text_input("Department")
                    max_hours = st.number_input("Max Hours per Week", min_value=1, max_value=40, value=20)
                with col2:
                    password = st.text_input("Password", type="password")
                    confirm_password = st.text_input("Confirm Password", type="password")
                    expertise = st.text_area("Expertise (comma-separated)")
                
                if st.form_submit_button("Add Staff Member", type="primary"):
                    if not all([name, username, password, confirm_password]):
                        st.error("Please fill all required fields!")
                    elif password != confirm_password:
                        st.error("Passwords don't match!")
                    elif len(password) < 4:
                        st.error("Password must be at least 4 characters!")
                    elif " " in username:
                        st.error("Username cannot contain spaces!")
                    else:
                        if faculty_collection.find_one({"username": username.lower()}):
                            st.error("Username already exists! Please choose a different one.")
                        else:
                            staff_data = {
                                "_id": username.lower(),
                                "name": name,
                                "username": username.lower(),
                                "department": department,
                                "expertise": [e.strip() for e in expertise.split(",") if e.strip()],
                                "max_hours": max_hours,
                                "admin": False,
                                "password": password,
                                "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            try:
                                faculty_collection.insert_one(staff_data)
                                st.success(f"Staff member '{name}' added successfully!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error adding staff member: {str(e)}")
            
            st.subheader("Current Staff Members")
            staff_list = get_staff_only()
            if staff_list:
                for f in staff_list:
                    with st.expander(f"{f['name']} (ID: {f['_id']})"):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"*Name:* {f['name']}")
                            st.write(f"*Staff ID:* {f['_id']}")
                            st.write(f"*Username:* {f.get('username', 'N/A')}")
                            st.write(f"*Department:* {f.get('department', 'N/A')}")
                            st.write(f"*Max Hours:* {f.get('max_hours', 'N/A')}")
                        with col2:
                            st.write(f"*Expertise:* {', '.join(f.get('expertise', []))}")
                            st.write(f"*Created:* {f.get('created_date', 'N/A')}")
                            current_classes = timetable_collection.count_documents({"faculty_id": f["_id"]})
                            current_duties = duties_collection.count_documents({"faculty_id": f["_id"]})
                            st.write(f"*Classes:* {current_classes}")
                            st.write(f"*Duties:* {current_duties}")
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button(f"Remove", key=f"remove_staff_{f['_id']}", type="secondary"):
                                faculty_collection.delete_one({"_id": f["_id"]})
                                timetable_collection.delete_many({"faculty_id": f["_id"]})
                                duties_collection.delete_many({"faculty_id": f["_id"]})
                                st.success(f"{f['name']} removed from staff!")
                                st.rerun()
                        
                        with col2:
                            if st.button(f"Reset Password", key=f"reset_staff_pass_{f['_id']}", type="secondary"):
                                st.session_state[f"show_staff_reset_{f['_id']}"] = True
                        
                        if st.session_state.get(f"show_staff_reset_{f['_id']}", False):
                            with st.form(f"reset_staff_form_{f['_id']}"):
                                new_password = st.text_input("New Password", type="password", key=f"new_staff_pass_{f['_id']}")
                                confirm_new = st.text_input("Confirm New Password", type="password", key=f"confirm_staff_pass_{f['_id']}")
                                
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.form_submit_button("Update Password"):
                                        if new_password and new_password == confirm_new:
                                            if len(new_password) >= 4:
                                                faculty_collection.update_one(
                                                    {"_id": f["_id"]}, 
                                                    {"$set": {"password": new_password}}
                                                )
                                                st.success(f"Password updated for {f['name']}!")
                                                st.session_state[f"show_staff_reset_{f['_id']}"] = False
                                                st.rerun()
                                            else:
                                                st.error("Password must be at least 4 characters!")
                                        else:
                                            st.error("Passwords don't match!")
                                with col2:
                                    if st.form_submit_button("Cancel"):
                                        st.session_state[f"show_staff_reset_{f['_id']}"] = False
                                        st.rerun()
            else:
                st.info("No staff members found.")
        
        with tab2:
            st.subheader("Add New Administrator")
            with st.form("add_admin"):
                col1, col2 = st.columns(2)
                with col1:
                    admin_name = st.text_input("Full Name")
                    admin_username = st.text_input("Username")
                    admin_department = st.text_input("Department")
                with col2:
                    admin_password = st.text_input("Password", type="password")
                    admin_confirm_password = st.text_input("Confirm Password", type="password")
                
                if st.form_submit_button("Add Administrator", type="primary"):
                    if not all([admin_name, admin_username, admin_password, admin_confirm_password]):
                        st.error("Please fill all required fields!")
                    elif admin_password != admin_confirm_password:
                        st.error("Passwords don't match!")
                    elif len(admin_password) < 4:
                        st.error("Password must be at least 4 characters!")
                    elif " " in admin_username:
                        st.error("Username cannot contain spaces!")
                    else:
                        if faculty_collection.find_one({"username": admin_username.lower()}):
                            st.error("Username already exists! Please choose a different one.")
                        else:
                            admin_data = {
                                "_id": admin_username.lower(),
                                "name": admin_name,
                                "username": admin_username.lower(),
                                "department": admin_department,
                                "expertise": [],
                                "max_hours": 0,
                                "admin": True,
                                "password": admin_password,
                                "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            try:
                                faculty_collection.insert_one(admin_data)
                                st.success(f"Administrator '{admin_name}' added successfully!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error adding administrator: {str(e)}")
            
            st.subheader("Current Administrators")
            admin_list = get_admin_only()
            if admin_list:
                for f in admin_list:
                    with st.expander(f"{f['name']} (ID: {f['_id']})"):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"*Name:* {f['name']}")
                            st.write(f"*Admin ID:* {f['_id']}")
                            st.write(f"*Username:* {f.get('username', 'N/A')}")
                            st.write(f"*Department:* {f.get('department', 'N/A')}")
                        with col2:
                            st.write(f"*Role:* Administrator")
                            st.write(f"*Created:* {f.get('created_date', 'N/A')}")
                        
                        if f["_id"] != st.session_state.current_user["_id"]:
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button(f"Remove Admin", key=f"remove_admin_{f['_id']}", type="secondary"):
                                    faculty_collection.delete_one({"_id": f["_id"]})
                                    st.success(f"{f['name']} removed from administrators!")
                                    st.rerun()
                            
                            with col2:
                                if st.button(f"Reset Password", key=f"reset_admin_pass_{f['_id']}", type="secondary"):
                                    st.session_state[f"show_admin_reset_{f['_id']}"] = True
                            
                            if st.session_state.get(f"show_admin_reset_{f['_id']}", False):
                                with st.form(f"reset_admin_form_{f['_id']}"):
                                    new_password = st.text_input("New Password", type="password", key=f"new_admin_pass_{f['_id']}")
                                    confirm_new = st.text_input("Confirm New Password", type="password", key=f"confirm_admin_pass_{f['_id']}")
                                    
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        if st.form_submit_button("Update Password"):
                                            if new_password and new_password == confirm_new:
                                                if len(new_password) >= 4:
                                                    faculty_collection.update_one(
                                                        {"_id": f["_id"]}, 
                                                        {"$set": {"password": new_password}}
                                                    )
                                                    st.success(f"Password updated for {f['name']}!")
                                                    st.session_state[f"show_admin_reset_{f['_id']}"] = False
                                                    st.rerun()
                                                else:
                                                    st.error("Password must be at least 4 characters!")
                                            else:
                                                st.error("Passwords don't match!")
                                    with col2:
                                        if st.form_submit_button("Cancel"):
                                            st.session_state[f"show_admin_reset_{f['_id']}"] = False
                                            st.rerun()
                        else:
                            st.info("This is your account (cannot remove self)")
            else:
                st.info("No administrators found.")
        
        with tab3:
            st.subheader("Add Subject")
            with st.form("add_subject"):
                subject_code = st.text_input("Subject Code")
                subject_name = st.text_input("Subject Name")
                hours_per_week = st.number_input("Hours per Week", min_value=1, max_value=10, value=3)
                time_slot = st.selectbox("Time Slot", [
                    "", "9:00-10:00", "10:00-11:00", "11:00-12:00", 
                    "2:00-3:00", "3:00-4:00", "4:00-5:00"
                ])
                days = st.multiselect("Days", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
                
                if st.form_submit_button("Add Subject"):
                    if subject_code and subject_name and days and time_slot:
                        subject_data = {
                            "_id": subject_code.upper(),
                            "subject_code": subject_code.upper(),
                            "subject_name": subject_name,
                            "hours_per_week": hours_per_week,
                            "time_slot": time_slot,
                            "days": days
                        }
                        try:
                            subjects_collection.insert_one(subject_data)
                            st.success(f"Subject '{subject_code}' added!")
                            st.rerun()
                        except:
                            st.error("Subject already exists!")
                    else:
                        st.error("Please fill all fields!")
            
            st.subheader("Current Subjects")
            subjects_list = list(subjects_collection.find())
            if subjects_list:
                for s in subjects_list:
                    with st.expander(f"{s['subject_code']} - {s['subject_name']}"):
                        st.write(f"*Hours/Week:* {s['hours_per_week']}")
                        st.write(f"*Time:* {s['time_slot']}")
                        st.write(f"*Days:* {', '.join(s['days'])}")
                        if st.button(f"Remove {s['subject_code']}", key=f"remove_subject_{s['_id']}"):
                            subjects_collection.delete_one({"_id": s["_id"]})
                            timetable_collection.delete_many({"subject_code": s["_id"]})
                            st.rerun()

    elif page == "Automatic Timetable":
        st.header("Automatic Timetable Creation (Staff Only)")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if st.button("Generate Clash-Free Timetable", type="primary"):
                timetable_collection.delete_many({})
                
                staff_list = get_staff_only()
                subjects_list = list(subjects_collection.find())
                
                if not staff_list or not subjects_list:
                    st.error("Please add staff members and subjects first!")
                else:
                    scheduled = 0
                    conflicts = 0
                    
                    for subject in subjects_list:
                        best_staff = None
                        best_score = -1
                        
                        for staff in staff_list:
                            if check_time_conflict(staff["_id"], subject["time_slot"], subject["days"]):
                                continue
                            
                            current_hours = sum(t.get("hours_per_week", 0) 
                                              for t in timetable_collection.find({"faculty_id": staff["_id"]}))
                            
                            if current_hours + subject["hours_per_week"] > staff.get("max_hours", 20):
                                continue
                            
                            expertise_score = get_expertise_match(staff["_id"], subject["subject_name"])
                            workload_score = (staff.get("max_hours", 20) - current_hours) * 2
                            score = expertise_score + workload_score
                            
                            if score > best_score:
                                best_score = score
                                best_staff = staff
                        
                        if best_staff:
                            timetable_data = {
                                "faculty_id": best_staff["_id"],
                                "faculty_name": best_staff["name"],
                                "subject_code": subject["subject_code"],
                                "subject": subject["subject_name"],
                                "time_slot": subject["time_slot"],
                                "days": subject["days"],
                                "hours_per_week": subject["hours_per_week"]
                            }
                            timetable_collection.insert_one(timetable_data)
                            scheduled += 1
                        else:
                            conflicts += 1
                    
                    st.success(f"Scheduled: {scheduled} subjects to staff members")
                    if conflicts > 0:
                        st.warning(f"Conflicts: {conflicts} subjects couldn't be scheduled")
                    
                    st.info("Note: Administrators are not included in teaching assignments")
                    st.rerun()
        
        with col2:
            timetable_df = export_timetable_to_csv()
            if timetable_df is not None:
                csv = timetable_df.to_csv(index=False)
                st.download_button(
                    label="Export Timetable CSV",
                    data=csv,
                    file_name=f"timetable_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
        
        st.subheader("Current Timetable")
        timetable_list = list(timetable_collection.find())
        if timetable_list:
            display_data = []
            for t in timetable_list:
                display_data.append({
                    "Staff ID": t["faculty_id"],
                    "Staff Name": t["faculty_name"],
                    "Subject Code": t["subject_code"],
                    "Subject": t["subject"],
                    "Time": t["time_slot"],
                    "Days": ", ".join(t["days"]),
                    "Hours/Week": t["hours_per_week"]
                })
            st.dataframe(pd.DataFrame(display_data), use_container_width=True)
        else:
            st.info("No timetable generated yet.")

    elif page == "Staff Workload Balance":
        st.header("Staff Workload Balance")
        
        staff_list = get_staff_only()
        if staff_list:
            workload_data = []
            for staff in staff_list:
                classes = list(timetable_collection.find({"faculty_id": staff["_id"]}))
                duties = list(duties_collection.find({"faculty_id": staff["_id"]}))
                
                teaching_hours = sum(c.get("hours_per_week", 0) for c in classes)
                duty_hours = sum(d.get("hours", 0) for d in duties)
                total_hours = teaching_hours + duty_hours
                max_hours = staff.get("max_hours", 20)
                
                workload_data.append({
                    "Staff ID": staff["_id"],
                    "Staff Member": staff["name"],
                    "Teaching Hours": teaching_hours,
                    "Duty Hours": duty_hours,
                    "Total Hours": total_hours,
                    "Max Hours": max_hours,
                    "Utilization %": round((total_hours/max_hours)*100, 1) if max_hours > 0 else 0
                })
            
            df = pd.DataFrame(workload_data)
            st.dataframe(df, use_container_width=True)
            
            # Create labels with Staff ID and Name for bar chart
            df["Display Name"] = df["Staff ID"] + " - " + df["Staff Member"]
            st.bar_chart(df.set_index("Display Name")["Total Hours"])
            
            st.info("Note: Only staff members are shown (administrators excluded)")
        else:
            st.info("No staff members found.")

    elif page == "Expertise Matching":
        st.header("Staff Expertise Matching")
        
        timetable_list = list(timetable_collection.find())
        if timetable_list:
            matching_data = []
            for assignment in timetable_list:
                faculty = faculty_collection.find_one({"_id": assignment["faculty_id"]})
                if faculty and not faculty.get("admin", False):
                    match_score = get_expertise_match(assignment["faculty_id"], assignment["subject"])
                    matching_data.append({
                        "Staff ID": assignment["faculty_id"],
                        "Staff Member": assignment["faculty_name"],
                        "Subject": assignment["subject"],
                        "Match Score": f"{match_score}%"
                    })
            
            if matching_data:
                df = pd.DataFrame(matching_data)
                st.dataframe(df, use_container_width=True)
                
                mismatches = [d for d in matching_data if int(d["Match Score"].replace("%", "")) < 70]
                if mismatches:
                    st.subheader("Low Expertise Matches")
                    st.dataframe(pd.DataFrame(mismatches), use_container_width=True)
                
                st.info("Note: Only staff assignments are shown (administrators excluded)")
            else:
                st.info("No staff timetable assignments found.")
        else:
            st.info("No timetable assignments found.")

    elif page == "Staff Burnout Monitor":
        st.header("Staff Burnout Index Monitoring")
        
        staff_list = get_staff_only()
        if staff_list:
            burnout_data = []
            for staff in staff_list:
                burnout = calculate_burnout_index(staff["_id"])
                status = "Good" if burnout < 40 else "Moderate" if burnout < 70 else "High Risk"
                
                burnout_data.append({
                    "Staff ID": staff["_id"],
                    "Staff Member": staff["name"],
                    "Burnout Index": burnout,
                    "Status": status
                })
            
            df = pd.DataFrame(burnout_data)
            st.dataframe(df, use_container_width=True)
            
            high_burnout = [d for d in burnout_data if d["Burnout Index"] >= 70]
            if high_burnout:
                st.error("High Burnout Alert!")
                for staff in high_burnout:
                    st.warning(f"{staff['Staff Member']} (ID: {staff['Staff ID']}) - Burnout Index: {staff['Burnout Index']}")
            
            st.info("Note: Only staff members are monitored (administrators excluded)")
        else:
            st.info("No staff members found.")

    elif page == "Non-teaching Duties":
        st.header("Non-teaching Duty Allocation (Staff Only)")
        
        tab1, tab2 = st.tabs(["Assign Duties", "View Duties"])
        
        with tab1:
            staff_list = get_staff_only()
            if staff_list:
                with st.form("assign_duty"):
                    col1, col2 = st.columns(2)
                    with col1:
                        staff_options = [""] + [f"{f['name']} (ID: {f['_id']})" for f in staff_list]
                        selected_staff_display = st.selectbox("Select Staff Member", staff_options)
                        duty_type_options = ["", "Exam Duty", "Placement Duty", "Admission Duty", 
                            "Event Coordination", "Lab Supervision", "Research Project",
                            "Committee Work", "External Review"]
                        duty_type = st.selectbox("Duty Type", duty_type_options)
                    with col2:
                        min_date = datetime.now().date()
                        date = st.date_input("Date", min_value=min_date, value=None)
                        hours = st.number_input("Hours", min_value=0, max_value=12, value=0)
                    
                    description = st.text_area("Description (Optional)")
                    
                    if st.form_submit_button("Assign Duty", type="primary"):
                        if not selected_staff_display or not duty_type or not date or hours == 0:
                            st.warning("Please enter all fields to assign.")
                        else:
                            selected_staff_name = selected_staff_display.split(" (ID: ")[0]
                            staff_data = next(f for f in staff_list if f["name"] == selected_staff_name)
                            date_str = str(date)
                            
                            existing_duty = duties_collection.find_one({
                                "faculty_id": staff_data["_id"],
                                "duty_type": duty_type,
                                "date": date_str
                            })
                            
                            if existing_duty:
                                duties_collection.update_one(
                                    {"_id": existing_duty["_id"]},
                                    {"$set": {
                                        "hours": hours,
                                        "description": description,
                                        "updated_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    }}
                                )
                                st.success(f"Duty updated for {selected_staff_name}!")
                            else:
                                duty_data = {
                                    "faculty_id": staff_data["_id"],
                                    "faculty_name": selected_staff_name,
                                    "duty_type": duty_type,
                                    "date": date_str,
                                    "hours": hours,
                                    "description": description,
                                    "assigned_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                }
                                duties_collection.insert_one(duty_data)
                                st.success(f"Duty assigned to {selected_staff_name}!")
                            st.rerun()
            else:
                st.info("No staff members found.")
        
        with tab2:
            staff_ids = [s["_id"] for s in get_staff_only()]
            duties_list = list(duties_collection.find({"faculty_id": {"$in": staff_ids}}))
            
            if duties_list:
                df = pd.DataFrame([{
                    "Staff ID": d["faculty_id"],
                    "Staff Member": d["faculty_name"],
                    "Duty": d["duty_type"],
                    "Date": d.get("date", "N/A"),
                    "Hours": d["hours"],
                    "Description": d.get("description", "N/A")[:50] + "..." if len(d.get("description", "")) > 50 else d.get("description", "N/A")
                } for d in duties_list])
                st.dataframe(df, use_container_width=True)
                
                st.subheader("Duty Summary")
                duty_summary = {}
                for duty in duties_list:
                    duty_type = duty["duty_type"]
                    duty_summary[duty_type] = duty_summary.get(duty_type, 0) + 1
                
                if duty_summary:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("*Duty Distribution:*")
                        for duty_type, count in duty_summary.items():
                            st.write(f"• {duty_type}: {count}")
                    with col2:
                        st.bar_chart(pd.DataFrame(list(duty_summary.items()), columns=["Duty Type", "Count"]).set_index("Duty Type"))
                
                st.info("Note: Only staff duties are shown (administrators excluded)")
            else:
                st.info("No duties assigned to staff members yet.")

    elif page == "System Reports":
        st.header("System Reports & Analytics")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            staff_count = faculty_collection.count_documents({"admin": {"$ne": True}})
            st.metric("Staff Members", staff_count)
        with col2:
            admin_count = faculty_collection.count_documents({"admin": True})
            st.metric("Administrators", admin_count)
        with col3:
            subjects_count = subjects_collection.count_documents({})
            st.metric("Total Subjects", subjects_count)
        with col4:
            scheduled_count = timetable_collection.count_documents({})
            st.metric("Scheduled Classes", scheduled_count)
        
        st.subheader("Staff Workload Summary")
        staff_list = get_staff_only()
        
        if staff_list:
            summary_data = []
            total_teaching_hours = 0
            total_duty_hours = 0
            
            for staff in staff_list:
                classes = list(timetable_collection.find({"faculty_id": staff["_id"]}))
                duties = list(duties_collection.find({"faculty_id": staff["_id"]}))
                
                teaching_hours = sum(c.get("hours_per_week", 0) for c in classes)
                duty_hours = sum(d.get("hours", 0) for d in duties)
                burnout = calculate_burnout_index(staff["_id"])
                
                total_teaching_hours += teaching_hours
                total_duty_hours += duty_hours
                
                summary_data.append({
                    "Staff ID": staff["_id"],
                    "Staff Member": staff["name"],
                    "Teaching": teaching_hours,
                    "Duties": duty_hours,
                    "Total": teaching_hours + duty_hours,
                    "Burnout": burnout
                })
            
            df = pd.DataFrame(summary_data)
            st.dataframe(df, use_container_width=True)
            
            st.subheader("System Efficiency Metrics")
            col1, col2, col3 = st.columns(3)
            with col1:
                avg_teaching = total_teaching_hours / len(staff_list) if staff_list else 0
                st.metric("Avg Teaching Hours", f"{avg_teaching:.1f}")
            with col2:
                avg_duties = total_duty_hours / len(staff_list) if staff_list else 0
                st.metric("Avg Duty Hours", f"{avg_duties:.1f}")
            with col3:
                unique_scheduled_subjects = len(timetable_collection.distinct("subject_code"))

                scheduled_percentage = (
                    unique_scheduled_subjects / subjects_count * 100
                ) if subjects_count > 0 else 0
                st.metric("Scheduling Efficiency", f"{scheduled_percentage:.1f}%")
            
            st.subheader("Recent Staff Activity")
            staff_ids = [s["_id"] for s in staff_list]
            recent_duties = list(duties_collection.find({"faculty_id": {"$in": staff_ids}}).sort("assigned_date", -1).limit(5))
            if recent_duties:
                for duty in recent_duties:
                    st.write(f"• *{duty['faculty_name']} (ID: {duty['faculty_id']})* assigned *{duty['duty_type']}* on {duty.get('date', 'N/A')}")
            else:
                st.info("No recent staff activity found.")
            
            st.info("Note: All reports exclude administrators (admins don't teach or have duties)")
        else:
            st.info("No staff members found for reporting.")

# ============ STAFF PAGES ============
else:
    
    if page == "My Dashboard":
        st.header("My Staff Dashboard")
        
        faculty_data = st.session_state.current_user
        faculty_id = faculty_data["_id"]
        
        col1, col2, col3 = st.columns(3)
        with col1:
            classes_count = timetable_collection.count_documents({"faculty_id": faculty_id})
            st.metric("Classes Assigned", classes_count)
        with col2:
            duties_count = duties_collection.count_documents({"faculty_id": faculty_id})
            st.metric("Duties Assigned", duties_count)
        with col3:
            burnout = calculate_burnout_index(faculty_id)
            st.metric("Burnout Index", f"{burnout}/100")
        
        st.subheader("My Information")
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"*Name:* {faculty_data['name']}")
            st.write(f"*Staff ID:* {faculty_data['_id']}")
            st.write(f"*Department:* {faculty_data.get('department', 'N/A')}")
            st.write(f"*Max Hours:* {faculty_data.get('max_hours', 'N/A')}")
        with col2:
            st.write(f"*Expertise:* {', '.join(faculty_data.get('expertise', []))}")
            st.write(f"*Member Since:* {faculty_data.get('created_date', 'N/A')}")
        
        st.subheader("My Timetable")
        faculty_classes = list(timetable_collection.find({"faculty_id": faculty_id}))
        if faculty_classes:
            df = pd.DataFrame([{
                "Subject": c["subject"],
                "Time": c["time_slot"],
                "Days": ", ".join(c["days"]),
                "Hours/Week": c["hours_per_week"]
            } for c in faculty_classes])
            st.dataframe(df, use_container_width=True)
            
            total_teaching_hours = sum(c["hours_per_week"] for c in faculty_classes)
            st.info(f"Total Teaching Hours: {total_teaching_hours} hours/week")
        else:
            st.info("No classes assigned yet.")
        
        st.subheader("My Duties")
        faculty_duties = list(duties_collection.find({"faculty_id": faculty_id}))
        if faculty_duties:
            df = pd.DataFrame([{
                "Duty": d["duty_type"],
                "Date": d.get("date", "N/A"),
                "Hours": d["hours"],
                "Description": d.get("description", "N/A")
            } for d in faculty_duties])
            st.dataframe(df, use_container_width=True)
            
            total_duty_hours = sum(d["hours"] for d in faculty_duties)
            st.info(f"Total Duty Hours: {total_duty_hours} hours")
        else:
            st.info("No duties assigned yet.")

    elif page == "My Burnout Index":
        st.header("My Burnout Index")
        
        faculty_id = st.session_state.current_user["_id"]
        burnout = calculate_burnout_index(faculty_id)
        
        if burnout < 40:
            st.success(f"Your Burnout Index: {burnout}/100 - Good")
            st.info("You have a healthy workload balance!")
        elif burnout < 70:
            st.warning(f"Your Burnout Index: {burnout}/100 - Moderate")
            st.info("Consider monitoring your workload and taking breaks.")
        else:
            st.error(f"Your Burnout Index: {burnout}/100 - High Risk")
            st.error("Your workload is very high. Please speak with administration about workload management.")
        
        st.subheader("My Workload Breakdown")
        
        classes = list(timetable_collection.find({"faculty_id": faculty_id}))
        duties = list(duties_collection.find({"faculty_id": faculty_id}))
        
        teaching_hours = sum(c.get("hours_per_week", 0) for c in classes)
        duty_hours = sum(d.get("hours", 0) for d in duties)
        total_hours = teaching_hours + duty_hours
        max_hours = st.session_state.current_user.get("max_hours", 20)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Teaching Hours/Week", teaching_hours)
        with col2:
            st.metric("Duty Hours", duty_hours)
        with col3:
            utilization = (total_hours / max_hours * 100) if max_hours > 0 else 0
            st.metric("Workload Utilization", f"{utilization:.1f}%")
        
        st.subheader("Recommendations")
        if burnout < 40:
            st.success("• Keep up the good work with your current workload balance!")
            st.success("• You may be able to take on additional responsibilities if needed.")
        elif burnout < 70:
            st.warning("• Consider discussing workload balance with your supervisor.")
            st.warning("• Take regular breaks and practice stress management.")
            st.warning("• Monitor your schedule for any upcoming heavy periods.")
        else:
            st.error("• Immediate discussion with administration recommended.")
            st.error("• Consider requesting workload redistribution.")
            st.error("• Prioritize essential tasks and delegate where possible.")
            st.error("• Take care of your mental and physical health.")

    elif page == "My Reports":
        st.header("My Personal Reports")
        
        faculty_id = st.session_state.current_user["_id"]
        faculty_data = st.session_state.current_user
        
        st.subheader("Personal Summary")
        
        classes = list(timetable_collection.find({"faculty_id": faculty_id}))
        duties = list(duties_collection.find({"faculty_id": faculty_id}))
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Classes Teaching", len(classes))
        with col2:
            st.metric("Duties Assigned", len(duties))
        with col3:
            total_teaching = sum(c.get("hours_per_week", 0) for c in classes)
            st.metric("Teaching Hours/Week", total_teaching)
        with col4:
            total_duties = sum(d.get("hours", 0) for d in duties)
            st.metric("Total Duty Hours", total_duties)
        
        st.subheader("My Expertise Utilization")
        if classes:
            expertise_data = []
            my_expertise = faculty_data.get("expertise", [])
            
            for class_item in classes:
                match_score = get_expertise_match(faculty_id, class_item["subject"])
                is_expertise = any(exp.lower() in class_item["subject"].lower() for exp in my_expertise)
                
                expertise_data.append({
                    "Subject": class_item["subject"],
                    "Match Score": f"{match_score}%",
                    "In My Expertise": "Yes" if is_expertise else "No"
                })
            
            df = pd.DataFrame(expertise_data)
            st.dataframe(df, use_container_width=True)
            
            high_match = len([d for d in expertise_data if int(d["Match Score"].replace("%", "")) >= 70])
            total_subjects = len(expertise_data)
            expertise_percentage = (high_match / total_subjects * 100) if total_subjects > 0 else 0
            
            st.info(f"Expertise Match Rate: {expertise_percentage:.1f}% ({high_match}/{total_subjects} subjects)")
        else:
            st.info("No classes assigned yet to analyze expertise utilization.")
        
        st.subheader("My Schedule Overview")
        if classes:
            schedule_data = []
            for class_item in classes:
                for day in class_item["days"]:
                    schedule_data.append({
                        "Day": day,
                        "Time": class_item["time_slot"],
                        "Subject": class_item["subject"],
                        "Hours": class_item["hours_per_week"]
                    })
            
            if schedule_data:
                df = pd.DataFrame(schedule_data)
                day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                df["Day"] = pd.Categorical(df["Day"], categories=day_order, ordered=True)
                df = df.sort_values(["Day", "Time"])
                
                st.dataframe(df, use_container_width=True)
                
                st.subheader("Weekly Distribution")
                day_hours = df.groupby("Day")["Hours"].sum().reset_index()
                st.bar_chart(day_hours.set_index("Day"))
        else:
            st.info("No schedule data available.")
        
        st.subheader("My Performance Indicators")
        max_hours = faculty_data.get("max_hours", 20)
        current_teaching = sum(c.get("hours_per_week", 0) for c in classes)
        current_duties = sum(d.get("hours", 0) for d in duties)
        total_workload = current_teaching + current_duties
        
        col1, col2 = st.columns(2)
        with col1:
            utilization = (total_workload / max_hours * 100) if max_hours > 0 else 0
            if utilization < 70:
                st.success(f"Workload Utilization: {utilization:.1f}% - Efficient")
            elif utilization < 90:
                st.warning(f"Workload Utilization: {utilization:.1f}% - High")
            else:
                st.error(f"Workload Utilization: {utilization:.1f}% - Overloaded")
        
        with col2:
            burnout = calculate_burnout_index(faculty_id)
            if burnout < 40:
                st.success(f"Burnout Risk: {burnout}/100 - Low")
            elif burnout < 70:
                st.warning(f"Burnout Risk: {burnout}/100 - Medium")
            else:
                st.error(f"Burnout Risk: {burnout}/100 - High")
