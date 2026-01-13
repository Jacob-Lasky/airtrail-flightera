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
    Searches for a specific flight in a list of flights.

    Args:
        flights: A list of flight dictionaries.
        flight_number: The flight number to search for (e.g., "DL3181").
        departure_date: The departure date in "YYYY-MM-DD" format.
        departing_airport: The IATA or ICAO code of the departing airport.

    Returns:
        A dictionary containing the flight data if found, otherwise None.
    """
    normalized_flight_number = flight_number.replace(" ", "").upper()
    normalized_departing_airport = departing_airport.upper()

    for flight in flights:
        # Handle cases where flightNumber is explicitly None in the data
        flight_num_raw = flight.get('flightNumber')
        current_flight_number = flight_num_raw.replace(" ", "").upper() if flight_num_raw else ""

        from_data = flight.get('from', {})
        iata = from_data.get('iata')
        icao = from_data.get('icao')

        if (
            current_flight_number == normalized_flight_number and
            flight.get('date') == departure_date and
            (
                (iata and iata.upper() == normalized_departing_airport) or
                (icao and icao.upper() == normalized_departing_airport)
            )
        ):
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

def main():
    parser = argparse.ArgumentParser(description="Search for a flight in the Airtrail database.")
    parser.add_argument('--id', type=int, help='The unique ID of the flight to fetch directly.')
    parser.add_argument('--flight-number', help='The flight number to search for (e.g., \"DL 5450\").')
    parser.add_argument('--date', help='The departure date for the search (YYYY-MM-DD).')
    parser.add_argument('--airport', help='The departing airport IATA/ICAO code for the search.')
    args = parser.parse_args()

    search_params_present = args.flight_number and args.date and args.airport
    if not args.id and not search_params_present:
        parser.error('Either --id or all of --flight-number, --date, and --airport are required.')

    api_key = os.getenv("AIRTRAIL_API_KEY")
    base_url = os.getenv("AIRTRAIL_BASE_URL")
    if not api_key:
        print("AIRTRAIL_API_KEY not found.")
        return False
    if not base_url:
        print("AIRTRAIL_BASE_URL not found.")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    found_flight = None

    if args.id:
        print(f"Fetching flight with ID: {args.id}")
        found_flight = get_flight_by_id(args.id, base_url, headers)
    
    elif search_params_present:
        print(f"Searching for flight {args.flight_number} from {args.airport} on {args.date}")
        all_flights_url = f"{base_url}/api/flight/list"
        try:
            response = requests.get(all_flights_url, headers=headers)
            response.raise_for_status()
            json_response = response.json()
            if isinstance(json_response, dict) and 'flights' in json_response:
                all_flights = json_response['flights']
                found_flight = find_flight(all_flights, args.flight_number, args.date, args.airport)
            else:
                print("Error: Unexpected JSON response format. 'flights' key not found.")
                return
        except requests.exceptions.RequestException as e:
            print(f"Error fetching flight list: {e}")
            return
        except json.JSONDecodeError:
            print("Error: Failed to decode JSON response from the server.")
            return

    if found_flight:
        print("\n--- Flight Found! ---")
        print(json.dumps(found_flight, indent=2))

        flight_data_for_scraper = found_flight.get('flight') if 'success' in found_flight else found_flight
        if flight_data_for_scraper:
            scrape_flightera_info(flight_data_for_scraper, base_url, headers)

    else:
        print("\nFlight not found.")

def scrape_flightera_info(flight_data, base_url, headers):
    """
    Scrapes Flightera.net for additional flight information using Selenium.
    """
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
            # Pass the original flight data, not the nested one, to the update function
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

            # Details URL
            details_tag = container.find('a', href=lambda h: h and 'flight_details' in h)
            details_url = f"https://www.flightera.net{details_tag['href']}" if details_tag else None

            scraped_data = {
                "aircraft_name": aircraft_name,
                "aircraft_icao": aircraft_icao,
                "aircraft_reg": aircraft_reg,
                "departure_status": departure_status,
                "arrival_status": arrival_status,
                "details_url": details_url
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
        existing_note = payload.get('note') or payload.get('notes') # Check both for safety
        if existing_note:
            # Format with separator
            payload['note'] = f"{existing_note}\n----------\n" + "\n".join(new_notes)
        else:
            # Format without separator
            payload['note'] = "\n".join(new_notes)
        
        payload.pop('notes', None) # Clean up old plural key
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

if __name__ == "__main__":
    main()
