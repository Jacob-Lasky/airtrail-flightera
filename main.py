import requests
import os
from dotenv import load_dotenv
import json
import argparse

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
    else:
        print("\nFlight not found.")

if __name__ == "__main__":
    main()
