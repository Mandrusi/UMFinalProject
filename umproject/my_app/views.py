import requests # Handles http requests
import base64 # base64 encoding for sending BLOBs to template as text
import sys # for debug flushes
import math # used for floor() and ceil() functions
import time # used for time.sleep() to delay after loading web page
import hashlib # used for a checksum stored in the Image table
import re # used to parse srcset using a regular expression
from io import BytesIO # Handle binary data to save img_data to database
from .models import Image, Search # Search and Image models (objects for database)
from PIL import Image as PILImage # For raster based image manipulation
from django.shortcuts import render, redirect # For rendering templates with context data and returning HTTP responses
from django.conf import settings # To allow access to constants set in settings.py
from bs4 import BeautifulSoup # For parsing html content
from urllib.parse import urljoin, urlparse # For combining relative references to full URL
from django.http import HttpResponse # For determining HttpResponse types
from django.utils import timezone # For displaying timezone
from selenium import webdriver # for webscraping and screencapturing
from selenium.webdriver.common.by import By

# Used to output debug statements to the console as the program runs
def debug(str):
    print(f"DEBUG: {str}")
    sys.stdout.flush()
    return

# Handler function to get_web_response for index view
def get_web_response_handler(request, url_with_scheme, url_entered):
    # Get HTML content of the URL, but handle redirects manually, to update url to new location
    try:
        response = requests.get(url_with_scheme, allow_redirects=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        debug(f"Error trying to get {url_with_scheme}: {e}")
        return None, None, f"Failed to get {url_with_scheme}"

    debug(f"in index(), returned from requests.get with response={response}")

    if response.status_code in {200,201}: # if status ok or created (kinda ok)
        url = url_entered
    elif response.status_code in {301,302,307,308}: # temporary or permanent redirect
        url = response.headers['Location']
        parsed_url = urlparse(url) 
        scheme = parsed_url.scheme if parsed_url.scheme else 'http' # Figure out scheme (e.g. http)
        url = f"{scheme}://{parsed_url.netloc}{parsed_url.path}" # Rebuild URL

        try:
            response = requests.get(url)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            debug(f"Error trying to redirect to {response.headers['Location']}: {e}")
            return None, None, f"Failed to redirect from {url_entered}, to {url}"
 
        if response.status_code in {200,201}: # if redirected get() status ok or created (kinda ok)
            debug(f"Successfully redirected to {url}")
        else:
            debug(f"Failed redirect to get {url}, status code {response.status_code}")
            return None, None, f"Failed redirect to get {url_entered} HttpResponse:{response.status_code}"
    else:
        debug(f"Status code {response.status_code} trying to get {url_entered}")
        return render(request, 'fail.html',{'error_message': f"Could not get {url_entered} HttpResponse:{response.status_code}"})
    return url, response, None

def pick_an_image_from_srcset(image_srcset, page_url):
    # an img srcset in html will list URLs of the same image in different sizes,
    # separated by commas, to allow picking the best size for a layout.
    # if we pick an image that's many megabytes, it chokes saving to the database.
    # so we cap the size at an area of 100,000 pixels, and pick the biggest url in the 
    # set of images that's under 100,000. if there are none we skip this image.
    # it can be slow checking a lot of image sizes...if speed was more crucial, maybe
    # check just the first and last urls, as they're probably in ascending or descending
    # order by size, so one or the other might fit our criteria. 

    maximum_size_allowed = 100000    

    # sizes = image_srcset.split(',')
    # The split didn't work if srcset URLs contain comma, like 
    #  "https://www.google.com?TYPE=1,DIR 1x, https://www.google.com?TYPE=2,DIR 1x"
    # That's why this findall is used with a wicked regular expression.  
    sizes = re.findall(r'([^,\s][^,]*[^,\s])\s+([^,\s][^,]*[^,\s])', image_srcset)

    biggest_size = None
    biggest_url = None
    for size in sizes:
        # Get the image URL and size

# Could use some error checking on this request 
        url_to_check = size[0].strip()

        # Join the web page URL prefix to the image URL if the image URL is a relative link
        if not url_to_check.startswith('http'):
            url_to_check = urljoin(page_url, url_to_check)

        response = requests.get(url_to_check)
        image_size_bytes = len(response.content)

        debug(f".....Checking url={url_to_check} size={image_size_bytes}")
        # Update the biggest area and URL if necessary
        if image_size_bytes <= maximum_size_allowed and (biggest_size is None or image_size_bytes > biggest_size):
            biggest_size = image_size_bytes
            biggest_url = url_to_check

    if biggest_url is None:
        debug(f"none of the images were suitable")
        return

#    img['src'] = biggest_url
    img_url = biggest_url
    debug(f"chose best size from srcset, img_url = {img_url}, size={biggest_size}")
    return img_url

def retrieve_and_validate_img_handler(img_url):
# Retrieve the image data from the URL
    try:
        response = requests.get(img_url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        debug(f"Error retrieving image {img_url}: {e}")
        return None, None
    # Don't store images in database if over max size, currently 1024576.
    # It can handle bigger, but may affect performance at some level,
    # and that seems like a reasonable limit.
    maximium_size_to_save = 1024576

    image_size_bytes = len(response.content)
    if image_size_bytes > maximium_size_to_save:
        return None, None

    # Get image type based on file extension
    content_type = response.headers.get('content-type')
    if not content_type.startswith("image/"): # if not an image type of content, skip it
    #    debug(f"Invalid image content type for {img_url}: {content_type}")
        return None, None # Not image content_type (maybe "text/html") so skip to next loop iterator

    debug(f"content_type: {content_type}")
    return response, content_type

# Save the Image object to the database
def database_save_handler(image_data, search, img_url, content_type):
    debug(f"In database_save_handler, passed image_data, search (.id={search.id}), img_url={img_url}, content_type={content_type}")

    try:
        img_data = BytesIO(image_data)
    except Exception as e:
        debug(f"Error setting img_data to BytesIO() for {img_url}: {e}")
        return
    debug(f"got img_data from BytesIO, img_data={img_data}")

    unique_search_image = str(search.id) + '+' + hashlib.md5(image_data).hexdigest() # search_id + 32-character checksum of image data

    try:
        img_obj = Image(search=search, url=img_url[:255], image=img_data.getvalue(), content_type=content_type[:64], unique_search_image = unique_search_image[:64]) 
        debug(f"did Image() call") 
        img_obj.save()
        debug(f"did img_obj.save")
    except Exception as e:
        debug(f"Error saving image to database: {e}")
        return
    return True

# Retrieves and stores image from 'image_url' in database Image table, associating it with a search in Search table.
# 'page_url' is the web page from which the 'image_url' was found, needed in case 'image_URL' is a relative link.
def store_image_from_url_in_database(search,image_url,page_url):
    
    # Check parameters sent to function
    if search is None or image_url is None or image_url == '' or page_url is None:
        return

    # Join the web page URL prefix to the image URL if the image URL is a relative link
    if not image_url.startswith('http'):
        image_url = urljoin(page_url, image_url)
    
    # Call handler function for retrieving and validating img
    response, content_type = retrieve_and_validate_img_handler(image_url) 
    
    # If we got a response, call handler function for saving objects to database
    if response:
        database_save_handler(response.content, search, image_url, content_type) 

    return

def scrape_page_with_webdriver(search,url):

    debug(f"starting scrape_page_with_webdriver(url), url={url}")

    # Set Chrome webdriver options and instantiate the driver
    try:
        options = webdriver.ChromeOptions()   # Options for Chrome webdriver
        options.add_argument('--headless')    # Run in headless mode (no GUI)
        options.add_argument('--disable-gpu') # disable GPU usage (avoids some bugs)
        driver = webdriver.Chrome(executable_path=settings.CHROME_DRIVER_EXECUTABLE_LOCATION, options=options)
    except Exception as e:
        debug(f"Exception opening Chrome webdriver: {e}")

    # Set Firefox webdriver options and instantiate the driver
    # try:
    #     options = webdriver.FirefoxOptions() # Options for Firefox webdriver
    #     options.headless = True              # Run in headless mode (no GUI)
    #     options.binary_location = FIREFOX_BROWSER_EXECUTABLE_LOCATION # Actual firefox location
    #     driver = webdriver.Firefox(executable_path=FIREFOX_DRIVER_EXECUTABLE_LOCATION, options=options)
    # except Exception as e:
    #     debug(f"Exception opening Firefox webdriver: {e}")

    # Get URL using web driver
    debug(f"Load {url} with webdriver")
    try:
        driver.get(url)
    except Exception as e:
        debug(f"Exception opening URL {url} with webdriver: {e}")
        return
    
    debug(f"Loaded {url} in webdriver")

    # Set window size for webdriver (virtual window size, since it's operating in 'headless' mode)
    browser_width = 1920
    browser_height = 4000
    try:
        driver.set_window_size(browser_width, browser_height)
    except Exception as e:
        debug(f"Exception setting virtual window size webdriver: {e}")
        return
 
    # Sleep to give page time to load
    time.sleep(3)

    # NOTE: Tried using this to wait until Selenium reported the web elements were 
    # all visible, but didn't seem to work. Also tried with presence_of_all_elements.
    # Just using 3 second delay for now.
    #
    # Wait for all elements to be visible
    #
    # (from dependencies required for this):
    # from selenium.webdriver.support.ui import WebDriverWait
    # from selenium.webdriver.support import expected_conditions
    # from selenium.common.exceptions import TimeoutException
    # from selenium.webdriver.common.by import By
    #
    # try:
    #     images = WebDriverWait(driver, 10).until(expected_conditions.visibility_of_all_elements_located((By.TAG_NAME, 'img')))
    # except TimeoutException:
    #     return render(request, 'fail.html', {'error_message': f"Timed out waiting for web page elements to load"})

    cntr = 0
    screen_png = driver.get_screenshot_as_png() # saves screenshot of entire page
    screen_whole = PILImage.open(BytesIO(screen_png)) # uses PIL library to open image in memory
    
    # Save whole screen to a test0.png for debugging purposes
    screen_whole.save(f'test{cntr}.png')
    cntr = cntr + 1

    element_ctr = 1

    element_tags_to_process = ('img','svg') # WebElement tag_names to process

    # For loop does one pass processing all 'img' element types, then a second for all 'svg' element
    # types. Type 'img' provide URLs we can fetch, while 'svg' is used for inline SVG instructions on
    # a web page (draw circle at x,y, etc.), so in that case we crop the area from a screenshot
    for element_tag in element_tags_to_process:
        elements = driver.find_elements(By.TAG_NAME, element_tag)
        debug(f"Number of elements found: {len(elements)}")

        # Iterate over the web elements and store images if suitable
        for element in elements:
            try:
                element_displayed = element.is_displayed()
        
                # Skip elements that aren't displayed, have no location, or have width or height of zero 
                if not element_displayed or not element.location or \
                not element.size or element.size['height'] == 0 or element.size['width'] == 0:
                    continue

                if element_tag == 'img':
                    image_src = element.get_attribute('src')
                    image_srcset = element.get_attribute('srcset')
                elif element_tag == 'svg':
                    image_src = None
                    image_srcset = None
            except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
                debug("Got a stale element or other error processing WebElements from Selenium")
                continue

            element_ctr += 1
            if image_srcset and image_srcset != '':
                debug(f"IMG SRCSET element#{element_ctr} name={element.tag_name} text={element.text} size = {element.size} location = {element.location}")
                debug(f"           srcset={image_srcset}")

                image_src = pick_an_image_from_srcset(image_srcset, url)

                store_image_from_url_in_database(search, image_src, url)

            elif image_src and image_src != '':
                # image_filename = image_src.split('/')[-1]
                debug(f"IMG SRC    element#{element_ctr} name={element.tag_name} text={element.text} size = {element.size} location = {element.location}")
                debug(f"           image_src={image_src}")

                store_image_from_url_in_database(search, image_src, url)

            elif element.tag_name == 'svg':
                debug(f"SVG        element#{element_ctr} name={element.tag_name} text={element.text} size = {element.size} location = {element.location}")
                debug(f"           element={element}")
                # Calculate crop parameters. floor() and ceil() to round to integers in case it comes back float.
                left = math.floor(element.location['x'])
                top = math.floor(element.location['y'])
                right = left + math.ceil(element.size['width'])
                bottom = top + math.ceil(element.size['height'])
                debug(f"           left={left} top={top} right={right} bottom={bottom}")

                if bottom > browser_height or right > browser_width:
                    debug("            Image to crop is off the page")
                    continue

                screen_cropped = screen_whole.crop( (left, top, right, bottom) ) # get cropped subset of image
                debug(f"           Cropped")

                # Create a BytesIO object to store the image data as bytes
                image_buffer = BytesIO()

                # Save the cropped image to the BytesIO buffer
                screen_cropped.save(image_buffer, format='PNG')

                # Get the bytes value from the BytesIO buffer
                image_data = image_buffer.getvalue()

                #screen_cropped.save(f'test{cntr}.png')
                debug(f"           Saved to test{cntr}.png")  
                cntr += 1

                database_save_handler(image_data, search, '(screen shot)', 'image/x-png')

                # Note to self:
                # To print first 40 characters of binary data for debugging, b64encode it: 
                # {base64.b64encode(screen_cropped)[:40]}
                # {(base64.b64decode(screen_cropped.make_blob())[:40])}

    debug(f"quitting driver, went through {element_ctr} elements")
    # Close the browser instance
    try:
        driver.quit()
    except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
        debug("Got a stale element or other error processing WebElements from Selenium")
        return
    debug(f"quit driver")

    return

def home_page(request):
    try:
        searches = Search.objects.all()
        images = Image.objects.all()
    except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
        debug(f"Error in past_searches() retrieving searches or images from database: {e}")
        return render(request, 'fail.html', {'error_message': f"Error retrieving searches/images from database: {e}"})
    return render(request, 'index.html', {'number_of_searches': len(searches), 'number_of_images': len(images)}) 

def scrape_web_page(request):
    debug(f"Starting scrape_web_page(request), request={request}")

    if request.method == 'POST':

        url_entered = request.POST['url'] # url_entered gets URL user entered in the index.html form
        # Note that we store url_entered in the search database just as the user entered it, even if
        # it's just "google.com", and we wind up storing images from "https://www.google.com"

        parsed_url = urlparse(url_entered) # Parse the URL entered by the user to get the scheme
        scheme = parsed_url.scheme if parsed_url.scheme else 'http' # Figure out scheme (e.g. http)
        url_with_scheme = f"{scheme}://{parsed_url.netloc}{parsed_url.path}" # Rebuild URL
        
        url, response, error = get_web_response_handler(request,url_with_scheme, url_entered) # Call handler function for getting web response
        debug(f"get_web_response_handler() returned url={url}, response={response}, error={error}")
        if error:
            return render(request, 'fail.html', {'error_message': error})

        # We can retrieve a web page, so save the search URL (as the user entered it) in the Searches database
        try:
            search = Search.objects.create(url=url_entered) # Save the search instance
            search.save()
        except Exception as e:
            debug(f"Failure inserting Search record: {e}")
            return render(request, 'fail.html', {'error_message': f"Unable to insert search in database.html: {e}"})

        soup = BeautifulSoup(response.content, 'html.parser')  # Parse HTML content with BeautifulSoup
        img_tags = soup.find_all('img') # Extract all the image URLs from the HTML content

        for img in img_tags:
            if 'srcset' in img.attrs:
                debug(f"Picking an image from srcset={img['srcset']}")
                image_url = pick_an_image_from_srcset(img['srcset'],url) # Call handler function for srcset
            elif 'data-srcset' in img.attrs:
                debug(f"Picking an image from srcset={img['data-srcset']}")
                image_url = pick_an_image_from_srcset(img['data-srcset'],url) # Call handler function for srcset
            elif 'src' in img.attrs and img['src'] != '': # this is a simple <img src=...> tag
                debug(f"img={img}")
                debug(f"Using an image from image_url={img['src']}")
                image_url = img['src']
            else:
                debug(f"Skipping img in img_tags loop")
                continue  # Skip this image tag since it has no 'src' or 'srcset' attributes

            # Store the image at 'image_url' in Images table, with search data
            debug(f"In img_tags loop, about to store img_url = {image_url}")
            store_image_from_url_in_database(search,image_url,url)
            
        debug(f"Done with img_tags loop and beautiful soup scraping, about to call scrape_page+with_webdriver")
        debug(f"_____________________________________________________________________________________________")
        debug(f"_____________________________________________________________________________________________")
        scrape_page_with_webdriver(search,url)

        return redirect('success', id=search.id)
    return render(request, 'scrape_web_page.html')

def myimage(request, image_id):
    image = Image.objects.get(pk=image_id)
    return HttpResponse(image.image, content_type="image/jpeg")

def show_all_images(request):
    images = Image.objects.all()
    for image in images:
        img_data = image.image
       
        # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
        filename = image.url.split('?')[0]
        # split remaining url by / and pick the last (-1) element, which is just the filename
        filename = filename.split('/')[-1]
        image.filename = filename
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(img_data).decode('utf-8')}"
    return render(request, 'show_all_images.html', {'images': images}, )

# Show past_search.html 
def past_searches(request):
    # Get all past searches, format local timestamp, and send to template
    try:
        searches = Search.objects.all()
        images = Image.objects.all()
    except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
        debug(f"Error in past_searches() retrieving searches or images from database: {e}")
        return render(request, 'fail.html', {'error_message': f"Error retrieving searches/images from database: {e}"})

    for search in searches:
        local_tz = timezone.get_current_timezone()
        local_dt = search.timestamp.astimezone(local_tz)
        search.timestampadjuster = local_dt.strftime('%Y-%m-%d %H:%M:%S')
        
    return render(request, 'past_searches.html', {'searches': searches, 'number_of_images': len(images)}) # Render list of searches to template
    
# Show a page for a given past search, including its URL & timestamp, and images stored 
def past_search(request):
    try:
        # Check if an id parameter was sent (e.g. http://127.0.0.1:8000/past_searches?id=5) 
        search_id_for_page = request.GET['id']
    except KeyError:
        # No id sent to this as an attribute to the URL
        return render(request, 'fail.html', {'error_message': "No search id sent to past_search.html"})

    try:
        search = Search.objects.get(id=search_id_for_page)
    except Search.DoesNotExist:
        return render(request, 'fail.html',{'error_message': f"Search ID {search_id_for_page} not found"})

    local_tz = timezone.get_current_timezone()
    local_dt = search.timestamp.astimezone(local_tz)
    search.timestamp_local = local_dt.strftime('%Y-%m-%d %H:%M:%S')

    images = Image.objects.filter(search_id=search_id_for_page)

    for image in images:
        img_data = image.image
        # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
        filename = image.url.split('?')[0]
        # split remaining url by / and pick the last (-1) element, which is just the filename
        filename = filename.split('/')[-1]
        image.full_filename = filename # send full filename, and a max-30-character filename, to template
        if filename.rfind('.') == -1 and len(filename) > 30: # if no period in filename & > 30 chars, truncate to 26 chars + '(...)'
            image.filename = filename[:25] + '(...)'
        elif filename.rfind('.') > 0 and len(filename) > 30: # if period & prefix of filename is > 30 chars, truncate prefix + '(...)'
            image.filename = filename.split('.')[0][:21] + '(...).' + filename.split('.')[1][:4]
        else:
            image.filename = filename
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(img_data).decode('utf-8')}"
    debug(f"Returning past_search.html")
    # return render(request, 'past_search.html', {'images': images}, {'search_url': search_url})
    return render(request, 'past_search.html', {'images': images, 'search_url': search.url, 'search_timestamp': search.timestamp_local})

# Show success.html after storing images for user's requested URL
def success(request, id):
    debug(f"in success(), id={id} ")

    # Retrieve the Search record with the id of the search we just performed
    try:
        search = Search.objects.get(id=id)
    except Search.DoesNotExist:
        return render(request, 'fail.html', {'error_message': "Unexpected problem retrieving images from search"},)
    
    # Format the timestamp from the search as the local timezone  
    local_tz = timezone.get_current_timezone()
    local_dt = search.timestamp.astimezone(local_tz)
    search_timestamp_formatted = local_dt.strftime('%Y-%m-%d %H:%M:%S')

    # Retrieve all the Image records with the id of the search we just performed
    images = Image.objects.filter(search_id=id)

    # Set a filename and image_data_uri attribute for each image to send to success.html
    for image in images:
        img_data = image.image
        # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
        filename = image.url.split('?')[0]
        # split remaining url by / and pick the last (-1) element, which is just the filename
        filename = filename.split('/')[-1]
        image.full_filename = filename # send full filename, and a max-30-character filename, to template
        if filename.rfind('.') == -1 and len(filename) > 30: # if no period in filename & > 30 chars, truncate to 26 chars + '(...)'
            image.filename = filename[:25] + '(...)'
        elif filename.rfind('.') > 0 and len(filename) > 30: # if period & prefix of filename is > 30 chars, truncate prefix + '(...)'
            image.filename = filename.split('.')[0][:21] + '(...).' + filename.split('.')[1][:4]
        else:
            image.filename = filename
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(img_data).decode('utf-8')}"

    # Return success.html with data to render it (images, search.url, search_timestamp_formatted) 
    debug(f"Returning success.html")
    return render(request, 'success.html', {'images': images, 'search_url': search.url, 'search_timestamp': search_timestamp_formatted})