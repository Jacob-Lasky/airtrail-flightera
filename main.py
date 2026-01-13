import requests
import os
from dotenv import load_dotenv
import json
import argparse
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.firefox.options import Options
from bs4 import BeautifulSoup
import pytz

load_dotenv()

def find_flight(flights, flight_number, departure_date, departing_airport):
    """
    Searches for a specific flight in a list of flights, accounting for timezones.
    """
    search_flight_num = flight_number.replace(" ", "").lower()
    search_airport = departing_airport.lower()
    search_date_obj = datetime.strptime(departure_date, "%Y-%m-%d").date()

    for flight in flights:
        flight_number_val = flight.get('flightNumber')
        if not flight_number_val:
            continue

        flight_num = flight_number_val.replace(" ", "").lower()
        from_airport_icao = flight.get('from', {}).get('icao', '').lower()
        from_airport_iata = flight.get('from', {}).get('iata', '').lower()

        # Timezone-aware date comparison
        departure_utc_str = flight.get('departure')
        local_tz_str = flight.get('from', {}).get('tz')

        if not (departure_utc_str and local_tz_str):
            continue  # Skip if we don't have enough info

        try:
            utc_dt = datetime.fromisoformat(departure_utc_str.replace('Z', '+00:00'))
            local_tz = pytz.timezone(local_tz_str)
            local_dt = utc_dt.astimezone(local_tz)
            flight_local_date = local_dt.date()
        except (ValueError, pytz.UnknownTimeZoneError):
            continue # Skip if date/tz is invalid

        if (flight_num == search_flight_num and
            flight_local_date == search_date_obj and
            (from_airport_icao == search_airport or from_airport_iata == search_airport)):
            return flight
            
    return None

def get_flight_by_id(flight_id, base_url, headers):
    """
    Fetches a single flight by its ID from the API.
    """
    flight_url = f"{base_url}/api/flight/get/{flight_id}"
    try:
        response = requests.get(flight_url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching flight with ID {flight_id}: {e}")
        return None
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON response.")
        return None

def process_all_flights(base_url, headers):
    """
    Fetches all flights from the API and processes them.
    """
    all_flights_url = f"{base_url}/api/flight/list"
    try:
        response = requests.get(all_flights_url, headers=headers)
        response.raise_for_status()
        flights = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching all flights: {e}")
        return
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON response from the server.")
        return

    if isinstance(flights, dict) and 'flights' in flights:
        all_flights = flights['flights']
    else:
        print("Error: Unexpected JSON response format. 'flights' key not found.")
        return

    failures = []
    for flight in all_flights:
        try:
            scrape_flightera_info(flight, base_url, headers)
        except Exception as e:
            failures.append({
                'flight': flight,
                'error': str(e)
            })

    if failures:
        with open('flight_scraping_failures.json', 'w') as f:
            json.dump(failures, f, indent=2)
        print("Flight scraping failures logged to flight_scraping_failures.json")

def main():
    parser = argparse.ArgumentParser(description="Scrape flight data and update the Airtrail database.")
    parser.add_argument('--id', type=int, help='The unique ID of a specific flight to process.')
    parser.add_argument('--flight-number', help='The flight number to search for.')
    parser.add_argument('--date', help='The departure date for the search (YYYY-MM-DD).')
    parser.add_argument('--airport', help='Departure airport ICAO code for searching.')
    parser.add_argument('--all', action='store_true', help='Process all flights in the database.')
    args = parser.parse_args()

    base_url = os.getenv('AIRTRAIL_BASE_URL')
    api_key = os.getenv('AIRTRAIL_API_KEY')
    if not api_key or not base_url:
        print("Error: AIRTRAIL_BASE_URL and AIRTRAIL_API_KEY must be set in .env file.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}

    if args.all:
        process_all_flights(base_url, headers)
    elif args.id:
        print(f"--- Processing single flight by ID: {args.id} ---")
        try:
            # Correct endpoint: /api/flight/get/{id}
            response = requests.get(f"{base_url}/api/flight/get/{args.id}", headers=headers)
            response.raise_for_status()
            flight_data = response.json().get('flight')
            if flight_data:
                scrape_flightera_info(flight_data, base_url, headers)
            else:
                print("Flight not found.")
        except Exception as e:
            print(f"An error occurred while processing flight ID {args.id}: {e}")
    elif args.flight_number and args.date and args.airport:
        print(f"--- Searching for flight: {args.flight_number} on {args.date} at {args.airport} ---")
        all_flights = get_all_flights(base_url, headers)
        if all_flights:
            found_flight = find_flight(all_flights, args.flight_number, args.date, args.airport)
            if found_flight:
                print(f"Found matching flight with ID: {found_flight.get('id')}")
                scrape_flightera_info(found_flight, base_url, headers)
            else:
                print("Flight not found with the specified criteria.")
    else:
        print("No action specified. Use --id, --all, or a full search (--flight-number, --date, --airport).")

def scrape_flightera_info(flight_data, base_url, headers):
    """
    Scrapes Flightera.net for additional flight information using Selenium.
    """
    # --- Fail Fast for Future Flights ---
    departure_date_str = flight_data.get('date')
    if departure_date_str:
        flight_date = datetime.strptime(departure_date_str, "%Y-%m-%d").date()
        if flight_date > datetime.now().date():
            print(f"Skipping future flight on {departure_date_str}.")
            return

    print("\n--- Scraping Flightera.net with Selenium ---")
    airline_name = flight_data.get('airline', {}).get('name')
    flight_number = flight_data.get('flightNumber')
    departure_date_str = flight_data.get('date')

    if not all([airline_name, flight_number, departure_date_str]):
        print("Could not scrape: Missing airline, flight number, or date information.")
        return

    try:
        departure_date = datetime.strptime(departure_date_str, "%Y-%m-%d")
        month_year = departure_date.strftime("%b-%Y")
    except ValueError:
        print(f"Could not scrape: Invalid date format '{departure_date_str}'.")
        return

    airline_formatted = airline_name.replace(" ", "+")
    flight_num_formatted = flight_number.replace(" ", "")
    url = f"https://www.flightera.net/en/flight/{airline_formatted}/{flight_num_formatted}/{month_year}#flight_list"
    print(f"Scraping URL: {url}")

    # Setup headless Firefox browser with Selenium
    firefox_options = Options()
    firefox_options.add_argument("--headless")
    
    try:
        service = FirefoxService(GeckoDriverManager().install())
        driver = webdriver.Firefox(service=service, options=firefox_options)
        
        print("Fetching page with Selenium...")
        driver.get(url)
        
        # Let the page load completely
        driver.implicitly_wait(5) 

        html_content = driver.page_source
        scraped_data = parse_flight_html(html_content, departure_date_str)

        if scraped_data:
            # --- Validation Step ---
            original_airline = flight_data.get('airline', {}).get('name', '').strip()
            original_flight_num = flight_data.get('flightNumber', '').strip()
            scraped_airline = scraped_data.get('scraped_airline', '').strip()
            scraped_flight_num = scraped_data.get('scraped_flight_number', '').strip()

            if (original_airline.lower() != scraped_airline.lower() or original_flight_num.lower() != scraped_flight_num.lower()):
                print("\n--- WARNING: Data Mismatch Detected (likely a codeshare) ---")
                print(f"Original: {original_airline} {original_flight_num}")
                print(f"Scraped:  {scraped_airline} {scraped_flight_num}")
                print("Update skipped to prevent incorrect data merge.")
                return

            update_flight(flight_data, scraped_data, base_url, headers)

    except Exception as e:
        print(f"An error occurred while scraping with Selenium: {e}")
    finally:
        if 'driver' in locals() and driver:
            driver.quit()

def parse_flight_html(html, target_date_str):
    """
    Parses the Flightera HTML to find and extract details for a specific flight date.
    Returns a dictionary with the scraped data, or None if not found.
    """
    print("\n--- Parsing Scraped Flight Data ---")
    soup = BeautifulSoup(html, 'lxml')
    target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d")
    flight_containers = soup.find_all('div', class_='flex flex-col gap-3')

    for container in flight_containers:
        date_tag = container.find('a', class_=lambda c: c and 'text-sm' in c and 'font-medium' in c)
        if not date_tag:
            continue

        try:
            row_date_obj = datetime.strptime(date_tag.text.strip(), "%d. %b %Y")
        except ValueError:
            continue

        if row_date_obj.date() == target_date_obj.date():
            # Aircraft and Registration
            plane_tags = container.find_all('a', class_=lambda c: c and 'underline' in c)
            aircraft_name = None
            aircraft_icao = None
            aircraft_reg = None
            if len(plane_tags) > 0:
                aircraft_name_tag = plane_tags[0]
                aircraft_name = aircraft_name_tag.text.strip()
                href = aircraft_name_tag.get('href')
                if href:
                    aircraft_icao = href.split('/')[-1]
            if len(plane_tags) > 1:
                aircraft_reg = plane_tags[1].text.strip()

            # Departure/Arrival Status
            status_spans = container.find_all('span', class_=lambda c: c and ('bg-yellow-100' in c or 'bg-green-100' in c))
            departure_status = status_spans[1].text.strip() if len(status_spans) > 1 else None
            arrival_status = status_spans[2].text.strip() if len(status_spans) > 2 else None

            # Details URL and validation data from it
            details_tag = container.find('a', href=lambda h: h and 'flight_details' in h)
            details_url = f"https://www.flightera.net{details_tag['href']}" if details_tag else None
            scraped_airline = None
            scraped_flight_number = None
            if details_url:
                url_parts = details_url.split('/')
                if len(url_parts) > 6:
                    scraped_flight_number = url_parts[-3]
                    # Extract airline, replacing '+' with space
                    airline_part = url_parts[-4].split('-')[0]
                    scraped_airline = airline_part.replace('+', ' ')

            scraped_data = {
                "aircraft_name": aircraft_name,
                "aircraft_icao": aircraft_icao,
                "aircraft_reg": aircraft_reg,
                "departure_status": departure_status,
                "arrival_status": arrival_status,
                "details_url": details_url,
                "scraped_airline": scraped_airline,
                "scraped_flight_number": scraped_flight_number
            }
            print(f"\n--- Scraped Flight Details ---")
            print(json.dumps(scraped_data, indent=2))
            return scraped_data

    print("Could not find matching flight details in the scraped HTML.")
    return None

def update_flight(original_flight, scraped_data, base_url, headers):
    """
    Updates the flight in the database with new information.
    """
    print("\n--- Preparing Flight Update ---")
    
    # Start with a full copy of the original flight data to preserve all fields.
    payload = original_flight.copy()
    is_updated = False

    # --- Timezone Conversion ---
    if payload.get('departure') and payload.get('from', {}).get('tz'):
        utc_dt = datetime.fromisoformat(payload['departure'].replace('Z', '+00:00'))
        local_tz = pytz.timezone(payload['from']['tz'])
        local_dt = utc_dt.astimezone(local_tz)
        payload['departure'] = local_dt.strftime('%Y-%m-%d')
        payload['departureTime'] = local_dt.strftime('%H:%M')

    if payload.get('arrival') and payload.get('to', {}).get('tz'):
        utc_dt = datetime.fromisoformat(payload['arrival'].replace('Z', '+00:00'))
        local_tz = pytz.timezone(payload['to']['tz'])
        local_dt = utc_dt.astimezone(local_tz)
        payload['arrival'] = local_dt.strftime('%Y-%m-%d')
        payload['arrivalTime'] = local_dt.strftime('%H:%M')

    # --- Flatten nested objects for the API ---
    if isinstance(payload.get('from'), dict):
        payload['from'] = payload['from'].get('icao')
    if isinstance(payload.get('to'), dict):
        payload['to'] = payload['to'].get('icao')
    if isinstance(payload.get('airline'), dict):
        payload['airline'] = payload['airline'].get('icao')
    if isinstance(payload.get('aircraft'), dict):
        payload['aircraft'] = payload['aircraft'].get('icao')

    # --- Conditionally update scraped data ---
    if not payload.get('aircraft') and scraped_data.get('aircraft_icao'):
        payload['aircraft'] = scraped_data['aircraft_icao']
        print(f"Adding Aircraft ICAO: {scraped_data['aircraft_icao']}")
        is_updated = True

    if not payload.get('aircraftReg') and scraped_data.get('aircraft_reg'):
        payload['aircraftReg'] = scraped_data['aircraft_reg']
        print(f"Adding Registration: {scraped_data['aircraft_reg']}")
        is_updated = True

    new_notes = []
    if scraped_data.get('departure_status'):
        new_notes.append(f"Departure: {scraped_data['departure_status']}")
    if scraped_data.get('arrival_status'):
        new_notes.append(f"Arrival: {scraped_data['arrival_status']}")
    if scraped_data.get('details_url'):
        new_notes.append(f"Flightera: {scraped_data['details_url']}")

    if new_notes:
        # Clean up old scraped data from the existing note to prevent bloat
        existing_note = payload.get('note') or payload.get('notes')
        cleaned_note_lines = []
        if existing_note:
            for line in existing_note.split('\n'):
                if not line.strip().startswith(('Departure:', 'Arrival:', 'Flightera:', '----------')):
                    cleaned_note_lines.append(line)
        
        # Join the cleaned original note
        final_note = "\n".join(cleaned_note_lines).strip()

        # Build the new note string
        new_note_section = "\n".join(new_notes)
        if final_note:
            payload['note'] = f"{final_note}\n----------\n{new_note_section}"
        else:
            payload['note'] = new_note_section

        payload.pop('notes', None)  # Clean up old plural key
        print(f"Updating Note:\n{payload['note']}")
        is_updated = True

    # Clean up unnecessary fields that are not part of the save API schema
    for key in ['duration', 'aircraftId', 'airlineId', 'fromId', 'toId']:
        payload.pop(key, None)

    if not is_updated:
        print("No new information to update.")
        return

    # Send the update request
    update_url = f"{base_url}/api/flight/save"
    try:
        print(f"Sending update to {update_url}")
        response = requests.post(update_url, headers=headers, json=payload)
        response.raise_for_status()
        print("\n--- Update Successful ---")
        print(json.dumps(payload, indent=2))
        print(json.dumps(response.json(), indent=2))
    except requests.exceptions.RequestException as e:
        print(f"\nError updating flight: {e}")
        if e.response is not None:
            print(f"Response Body: {e.response.text}")
        print(json.dumps(payload, indent=2))

def get_all_flights(base_url, headers):
    """
    Fetches all flights from the database.
    """
    # Correct endpoint: /api/flight/list
    url = f"{base_url}/api/flight/list"
    print(f"Fetching all flights from {url}...")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get('flights', [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching all flights: {e}")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON response when fetching all flights.")
    return []

def process_all_flights(base_url, headers):
    """
    Processes all flights in the database, scraping and updating them.
    """
    all_flights = get_all_flights(base_url, headers)
    if not all_flights:
        print("No flights to process.")
        return

    error_log = []
    total_flights = len(all_flights)
    print(f"Found {total_flights} flights to process.")

    for i, flight in enumerate(all_flights):
        flight_id = flight.get('id')
        flight_num = flight.get('flightNumber')
        print(f"\n--- ({i+1}/{total_flights}) Processing Flight ID: {flight_id}, Number: {flight_num} ---")
        try:
            scrape_flightera_info(flight, base_url, headers)
        except Exception as e:
            print(f"An unexpected error occurred while processing flight ID {flight_id}: {e}")
            error_log.append({
                'flight_id': flight_id,
                'flight_data': flight,
                'error_message': str(e),
                'timestamp': datetime.now().isoformat()
            })

    if error_log:
        print(f"\n--- Processing Complete with {len(error_log)} Errors ---")
        error_file = 'flight_processing_errors.json'
        with open(error_file, 'w') as f:
            json.dump(error_log, f, indent=2)
        print(f"Errors have been logged to {error_file}")
    else:
        print("\n--- Processing Complete: All flights processed successfully! ---")

if __name__ == "__main__":
    main()
