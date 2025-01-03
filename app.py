from flask import Flask, render_template, request, redirect, url_for, make_response
from detailsSeatAvailability import main as detailsSeatAvailability, set_token
from datetime import datetime
import requests, os, json
from flask import session
from flask import after_this_request

app = Flask(__name__)
app.secret_key = "your_secret_key"

TOKEN_API_URL = "https://railspaapi.shohoz.com/v1.0/app/auth/sign-in"

STATION_NAME_MAPPING = {
    "Coxs Bazar": "Cox's Bazar"
}

def fetch_token(phone_number, password):
    payload = {"mobile_number": phone_number, "password": password}

    try:
        response = requests.post(TOKEN_API_URL, json=payload)
        if response.status_code == 422:
            raise Exception("Wrong credentials provided.")
        response.raise_for_status()
        data = response.json()
        token = data["data"]["token"]
        return token
    except requests.RequestException as e:
        raise Exception(f"Failed to fetch token: {e}")
    
@app.after_request
def add_cache_control_headers(response):
    if 'set-cookie' in response.headers:
        return response

    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def home():
    with open('stations_en.json', 'r', encoding='utf-8') as file:
        stations_data = json.load(file)
    stations_list = stations_data.get('stations', [])

    error = session.pop('error', None)
    form_values = session.pop('form_values', None)

    if form_values and form_values.get('date'):
        form_values['date'] = datetime.strptime(form_values['date'], '%Y-%m-%d').strftime('%Y-%m-%d')

    token = request.cookies.get('token')

    show_disclaimer = token is None

    return render_template(
        'index.html',
        token=token,
        stations=stations_list,
        error=error,
        form_values=form_values,
        show_disclaimer=show_disclaimer
    )


@app.route('/check_seats', methods=['POST'])
def check_seats():
    try:
        form_values = {
            'phone_number': request.form.get('phone_number', ''),
            'origin': request.form.get('origin', ''),
            'destination': request.form.get('destination', ''),
            'date': request.form.get('date', '')
        }

        form_values['origin'] = STATION_NAME_MAPPING.get(form_values['origin'], form_values['origin'])
        form_values['destination'] = STATION_NAME_MAPPING.get(form_values['destination'], form_values['destination'])

        session['form_values'] = form_values

        token = request.cookies.get('token')

        if not token:
            phone_number = request.form.get('phone_number')
            password = request.form.get('password')

            if not phone_number or not password:
                error = "Session expired. Please log in again."
                session['error'] = error
                return redirect(url_for('home'))

            try:
                token = fetch_token(phone_number, password)
            except Exception as e:
                error = str(e)
                session['error'] = error
                return redirect(url_for('home'))

            @after_this_request
            def set_cookie(response):
                response.set_cookie('token', token, httponly=True)
                return response

            set_token(token)
        else:
            set_token(token)

        raw_date = request.form['date']
        formatted_date = datetime.strptime(raw_date, '%Y-%m-%d').strftime('%d-%b-%Y')

        config = {
            'from_city': form_values['origin'],
            'to_city': form_values['destination'],
            'date_of_journey': formatted_date,
            'seat_class': 'S_CHAIR'
        }

        try:
            result = detailsSeatAvailability(config)
        except Exception as e:
            if "Token expired" in str(e) or "unauthorized" in str(e).lower():
                @after_this_request
                def clear_cookie(response):
                    response.delete_cookie('token')
                    return response

                error = "Authorization token expired. Please log in again."
                session['error'] = error
                return redirect(url_for('home'))

        if "error" in result:
            error = result["error"]
            session['error'] = error
            return redirect(url_for('home'))

        for train, details in result.items():
            details['from_station'] = config['from_city']
            details['to_station'] = config['to_city']

            for seat_type in details['seat_data']:
                seat_type['grouped_seats'] = group_by_prefix(seat_type['available_seats'])
                seat_type['grouped_booking_process'] = group_by_prefix(seat_type['booking_process_seats'])

        session['last_result'] = result

        return redirect(url_for('show_results'))

    except Exception as e:
        error = str(e)
        session['error'] = error
        return redirect(url_for('home'))

@app.route('/show_results')
def show_results():
    from flask import session
    result = session.pop('last_result', None)
    if result is None:
        return redirect(url_for('home'))

    form_values = session.get('form_values', {})
    origin = form_values.get('origin', '')
    destination = form_values.get('destination', '')

    raw_date = form_values.get('date', '')
    if raw_date:
        formatted_date = datetime.strptime(raw_date, '%Y-%m-%d').strftime('%d-%b-%Y')
    else:
        formatted_date = ''

    seat_class = 'S_CHAIR'

    if result:
        result = dict(sorted(
            result.items(),
            key=lambda item: datetime.strptime(item[1]['departure_time'], '%d %b, %I:%M %p')
        ))

    @after_this_request
    def add_headers(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    return render_template(
        'results.html',
        result=result,
        origin=origin,
        destination=destination,
        date=formatted_date,
        seat_class=seat_class
    )

@app.route('/clear_token', methods=['POST'])
def clear_token():
    """Clear the token cookie."""
    response = make_response(redirect(url_for('home')))
    response.delete_cookie('token')
    return response

def group_by_prefix(seats):
    groups = {}
    for seat in seats:
        prefix = seat.split('-')[0]
        groups.setdefault(prefix, []).append(seat)
    return {prefix: {"seats": seats, "count": len(seats)} for prefix, seats in groups.items()}

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))