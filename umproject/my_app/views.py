import requests # Handles http requests
import base64 # base64 encoding for sending BLOBs to template as text
import sys # for debug flushes
import math # used for floor() and ceil() functions
import time # used for time.sleep() to delay after loading web page
import hashlib # used for a checksum stored in the Image table
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
# Allows extraction of the final URL and the associated response, or handles and logs any errors that occur
# during the process.
def get_web_response_handler(request, url_with_scheme, url_entered):
    # Get HTML content of the URL, but handle redirects manually, to update url to new location
    try:
        response = requests.get(url_with_scheme, allow_redirects=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        debug(f"Error trying to get {url_with_scheme}: {e}")
        return None, None, f"Failed to get page: {e}"

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
            return None, None, f"Failed to redirect from {url_entered}, to {url}: {e}"
 
        if response.status_code in {200,201}: # if redirected get() status ok or created (kinda ok)
            debug(f"Successfully redirected to {url}")
        else:
            debug(f"Failed redirect to get {url}, status code {response.status_code}")
            return None, None, f"Failed redirect to get {url_entered} HttpResponse:{response.status_code}"
    else:
        debug(f"Status code {response.status_code} trying to get {url_entered}")
        return render(request, 'fail.html',{'error_message': f"Could not get {url_entered} HttpResponse:{response.status_code}"})
    return url, response, None

# The purpose of this function is to handle the srcset attribute of an img tag, extract the URL-size pairs, and select
# the URL with the largest size that passes certain checks. It ensures that images with excessive sizes are not chosen
# and that the selected URL is a valid image.
def pick_an_image_from_srcset(image_srcset, page_url):
    # an img srcset in html will list URLs of the same image in different sizes,
    # separated by commas, to allow picking the best size for a layout.
    # if we pick an image that's many megabytes, it chokes saving to the database.
    # so if the size is capped in retrieve_and_validate_img_handlr(), and we pick the 
    # biggest image url in the that wasn't filtered out. if there are none we skip this image.
    # it can be slow checking a lot of image sizes...if speed were more crucial, maybe
    # check just the first and last urls, as they're probably in ascending or descending
    # order by size, so one or the other might fit our criteria. 

    debug(f"In pick_an_image_from_srcset(), image_srcset={image_srcset} page_url={page_url}")

    biggest_size = 0
    biggest_url = None

    # Brute force way to parse image_srcset, first break as left and right of ' ',
    # then if there's another comma, take just the portion right of the comma.
    # That handles srcsets that have commas within the URLs, not to be confused
    # with commas separating URL-size pairs, as in: 
    #  "https://www.google.com?TYPE=1,DIR 1x, https://www.google.com?TYPE=2,DIR 2x"
    while ' ' in image_srcset: 
        url_to_check = image_srcset.split(' ')[0]
        image_srcset = after_substr(image_srcset,' ') # set it to everything after the ' '
        if ',' in image_srcset:
            image_srcset = after_substr(image_srcset,',').strip() # now set it to everything after the ','

        # Join the web page URL prefix to the image URL if the image URL is a relative link
        if not url_to_check.startswith('http') and not url_to_check.startswith('data:'):
            url_to_check = urljoin(page_url, url_to_check)

        response_content, content_type = retrieve_and_validate_img_handler(url_to_check) 
        if response_content is not None:
            image_size_bytes = len(response_content)
            
            debug(f"url_to_check={url_to_check[:40]}... was size={image_size_bytes}")
            # Update the biggest area and URL if necessary
            if image_size_bytes > biggest_size:
                biggest_size = image_size_bytes
                biggest_url = url_to_check

    if biggest_url is None:
        debug(f"None of the images were suitable")
        return

    img_url = biggest_url
    debug(f"Chose best size from srcset, img_url = {img_url}, size={biggest_size}")
    return img_url

# return everything after a given substring in a string
def after_substr(string, substring):
    index = string.find(substring)
    if index != -1:
        return string[index + len(substring):]
    else:
        return ""

# Retrieve the image data from the URL
def retrieve_and_validate_img_handler(img_url):

    # Don't store images in database if over max size, currently 1024576.
    # It can handle bigger, but may affect performance at some level,
    # and that seems like a reasonable limit.
    maximum_size_to_save = 2000000

    if img_url.split('/')[0] == 'data:image':
        # If img_url is simply data, like data:image/x-png;base64,iVBORw0KGgoAAAANSUh..., 
        # then that contains all the data we need without retrieving it from the web.

        debug(f"Using data:image from URL")
    
        if len(img_url) > maximum_size_to_save: # Return None if too big
            return None, None       

        # img_url is of the form "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgA..."
        
        content_type = after_substr(img_url,':').split(';')[0] # Extract "image/png" between the first ':' and first ';'
        base64_string = after_substr(img_url,',') # Extract the base64-encoded data after the first comma
        
        image_bytes = base64.b64decode(base64_string) # Convert the base64 string to bytes
        return image_bytes, content_type
    else:
        try:
            response = requests.get(img_url)
            response.raise_for_status()
        except Exception as e:
            debug(f"Error retrieving image {img_url}: {e}")
            return None, None
    
        if len(response.content) > maximum_size_to_save: # Return None if too big
            return None, None

        content_type = response.headers.get('content-type')
        if not content_type.startswith("image/"): # if not an image type of content, skip it
        #    debug(f"Invalid image content type for {img_url}: {content_type}")
            return None, None # Not image content_type (maybe "text/html") so skip to next loop iterator

        debug(f"retrieve_and_validate_img_handler() returning content_type: {content_type}")
        return response.content, content_type

# The purpose of this function is to handle the saving of image data into the database. It converts
# the image data into a `BytesIO` object, generates a unique identifier for the image, creates an `Image`
# object, and saves it to the database.
def database_save_handler(image_data, search, img_url, content_type):
    debug(f"In database_save_handler, passed image_data, search (.id={search.id}), img_url={img_url}, content_type={content_type}")

    try:
        img_data = BytesIO(image_data)
    except Exception as e:
        debug(f"Error setting img_data to BytesIO() for {img_url}: {e}")
        return
#    debug(f"got img_data from BytesIO, img_data={img_data}")

    unique_search_image = str(search.id) + '+' + hashlib.md5(image_data).hexdigest() # search_id + 32-character checksum of image data

    try:
        img_obj = Image(search=search, url=img_url[:255], image=img_data.getvalue(), content_type=content_type[:64], unique_search_image = unique_search_image[:64]) 
#        debug(f"did Image() call") 
        img_obj.save()
#        debug(f"did img_obj.save")
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
    response_content, content_type = retrieve_and_validate_img_handler(image_url) 
    
    # If we got a response, call handler function for saving objects to database
    if response_content:
        if len(image_url) > 255:
            image_url = image_url[:255]
        database_save_handler(response_content, search, image_url, content_type)
    return

# The purpose of this function is to use a webdriver to load a web page, capture a screenshot of the page,
# and process specific elements (img and svg) to save image URLs or image data to the database.
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
    browser_height = 6000
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

    screen_png = driver.get_screenshot_as_png() # saves screenshot of entire page
    screen_whole = PILImage.open(BytesIO(screen_png)) # uses PIL library to open image in memory
    
    element_tags_to_process = ('img','svg') # WebElement tag_names to process

    # For loop does one pass processing all 'img' element types, then a second for all 'svg' element
    # types. Type 'img' provide URLs we can fetch, while 'svg' is used for inline SVG instructions on
    # a web page (draw circle at x,y, etc.), so in that case we crop the area from a screenshot
    for element_tag in element_tags_to_process:
#        elements = driver.find_elements_by_tag_name(element_tag)
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

            debug(f"IMG SRCSET element name={element.tag_name} text={element.text} size = {element.size} location = {element.location}")
            if element_tag == 'img':
                # Add url from image_src (if any) to image_srcset (if any), to compare image_srcset images all together
                
                if image_src and image_src != '':
                    if image_srcset and image_srcset == '':
                        image_srcset = image_src + ' 1x'
                    else:
                        image_srcset = image_src + ' 1x,' + image_srcset

                debug(f"           checking {image_srcset}")

                if image_srcset == '':
                    debug(f"Skipping img element loop (img={img})")
                    continue  # Skip this image tag since it has no 'src' or 'srcset' attributes
                else:
                    image_src = pick_an_image_from_srcset(image_srcset,url)
                    store_image_from_url_in_database(search, image_src, url)

            elif element_tag == 'svg':
                debug(f"           element={element}")
                
                # Calculate crop parameters. floor() and ceil() to round to integers in case it comes back float.
                left = math.floor(element.location['x'])
                top = math.floor(element.location['y'])
                right = left + math.ceil(element.size['width'])
                bottom = top + math.ceil(element.size['height'])
                # debug(f"           left={left} top={top} right={right} bottom={bottom}")

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

                database_save_handler(image_data, search, '(screen shot)', 'image/x-png')

                # Note to self:
                # To print first 40 characters of binary data for debugging, b64encode it:
                # {base64.b64encode(screen_cropped)[:40]}
                # {(base64.b64decode(screen_cropped.make_blob())[:40])}

    debug(f"quitting driver, went through {len(elements)} elements")
    # Close the browser instance
    try:
        driver.quit()
    except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
        debug("Got a stale element or other error processing WebElements from Selenium")
        return
    debug(f"quit driver")

    return

# The purpose of this function is to retrieve Search and Image objects from the database, calculate the number
# of searches and images, and render an HTML template with the information to be displayed in the browser.
def home_page(request):
    try:
        searches = Search.objects.all()
        images = Image.objects.all()
    except Exception as e: # (likely StaleElementReferenceException, but could be timeout or something else)
        debug(f"Error in past_searches() retrieving searches or images from database: {e}")
        return render(request, 'fail.html', {'error_message': f"Error retrieving searches/images from database: {e}"})
    return render(request, 'index.html', {'number_of_searches': len(searches), 'number_of_images': len(images)}) 

# The purpose of this function is to handle form submission, retrieve the URL entered by the user,
# perform web scraping operations using `BeautifulSoup`, and interact with the database to store the
# scraped data. It also calls other functions to handle HTTP requests, parse image tags, and perform
# additional web scraping using a web driver.
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
            img_str = str(img) # img by itself is an object, and we may want to use its string representation

            debug(f"checking img_str={img_str}")

            # Some sites use data-gl-src, data-gl-srcset, data-getimg, data-hi-res-src, data-full-url, full-src, and
            # a variety of other non-standard alternatives to src and srcset in image tags. (Example: usatoday.com).
            # So if we don't find ' src' or ' srcset', we'll use these variants instead if they're present.
            
            # Some img tags have a single src= and a multi-image srcset=, so find the single image url, and 
            # add it to a multi-image srcset url, so we pick an appropriately sized image from all hte candidates 

            if ' src="' in img_str: # priority if it has a space before it, in case of multiple src attributes
                single_image_url = after_substr(img_str,' src="').split('"')[0]
            elif 'src="' in img_str:
                single_image_url = after_substr(img_str,'src="').split('"')[0]
            elif 'url="' in img_str:
                single_image_url = after_substr(img_str,'url="').split('"')[0]
            elif 'img="' in img_str:
                single_image_url = after_substr(img_str,'img="').split('"')[0]
            else:
                single_image_url = ''

            if ' srcset="' in img_str: # priority if it has a space before it, in case of multiple srcset attributes
                multi_image_url = after_substr(img_str,'srcset="').split('"')[0]
            elif 'srcset="' in img_str:
                multi_image_url = after_substr(img_str,'srcset="').split('"')[0]
            else:
                multi_image_url = ''

            if single_image_url != '':
                if multi_image_url == '':
                    multi_image_url = single_image_url + ' 1x'
                else:
                    multi_image_url = single_image_url + ' 1x,' + multi_image_url

            if multi_image_url == '':
                debug(f"Skipping img in img_tags loop (img={img})")
                continue  # Skip this image tag since it has no 'src' or 'srcset' attributes

            image_url = pick_an_image_from_srcset(multi_image_url,url)
            
            # Store the image at 'image_url' in Images table, with search data
            debug(f"In img_tags loop, about to store img_url = {image_url} from img tag={img}")
            store_image_from_url_in_database(search,image_url,url)
            
        debug(f"Done with img_tags loop and beautiful soup scraping, about to call scrape_page+with_webdriver")
        debug(f"_____________________________________________________________________________________________")
        debug(f"_____________________________________________________________________________________________")
        scrape_page_with_webdriver(search,url)

        return redirect('success', id=search.id)
    return render(request, 'scrape_web_page.html')

# The purpose of this function is to serve images to the client by retrieving the image object
# from the database based on the provided `image_id`.
# It then returns an HTTP response with the image data and appropriate content_type, allowing the
# client to display the image in the browser or use it in other applications that consume image data.
def myimage(request, image_id):
    image = Image.objects.get(pk=image_id)
    return HttpResponse(image.image, content_type="image/jpeg")

# The purpose of this function is to retrieve images from the database, process them by extracting
# filenames and generating data URIs, and render a template to display the images in a web page.
def show_all_images(request):
    images = Image.objects.all()
    images = add_template_data_to_image(images)
    return render(request, 'show_all_images.html', {'images': images}, )

# The purpose of this function is to retrieve past searches and images from the database,
# adjust the timestamp to the local timezone, and render a template to display the searches
# along with the number of images in a web page. 
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
    
# Takes an Image record retrieved from database, and adds two fields to each 
# image record to display in the web page template:
# * image.filename (extracted from the URL, minus the http & domain name.
# * image.image_data_uri the image itself in data-formatted base64 encoding that
#   can be displayed with an <img> tag in the template.
def add_template_data_to_image(images):
    max_filename_length = 60 # max filename we display
        
    for image in images:
        if image.url.startswith("data"): 
            # if starts with 'data:' instead of 'http:' or 'https:', just use url as a pseudo-filename
            image.filename = image.url[:40] 
        else:
            # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
            filename = image.url.split('?')[0]
            # split remaining url by / and pick the last (-1) element, which is just the filename
            filename = filename.split('/')[-1]

            # Make a max-60-character filename to display as caption to template.
            # If it's say 100 characters ending in .jpeg, it will truncate the part before 
            # and add (...) but keep the .jpeg. If it doesn't have a period in it, it just
            # truncates it 5 characters before the size limit and adds (...).
            if filename == '':
                image.filename = '(no filename)' # if there was no text between the last / in the url and the ?, just include the whole url
            elif filename.rfind('.') == -1 and len(filename) > max_filename_length: # if no period in filename & > max chars, truncate to 26 chars + '(...)'
                image.filename = filename[:max_filename_length-5] + '(...)'
            elif filename.rfind('.') > 0 and len(filename) > max_filename_length: # if period & prefix of filename is > max chars, truncate prefix + '(...)'
                image.filename = filename.split('.')[0][:max_filename_length-9] + '(...).' + after_substr(filename,'.')[:4]
            else:
                image.filename = filename
            
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(image.image).decode('utf-8')}"

    return images

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

    # Add filename and image_data_uri attribute for each image to send to success.html
    images = add_template_data_to_image(images)

    # Return past_search.html with data to render it (images, search.url, search_timestamp_formatted) 
    debug(f"Returning past_search.html")
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

    # Add filename and image_data_uri attribute for each image to send to success.html
    images = add_template_data_to_image(images)

    # Return success.html with data to render it (images, search.url, search_timestamp_formatted) 
    debug(f"Returning success.html")
    return render(request, 'success.html', {'images': images, 'search_url': search.url, 'search_timestamp': search_timestamp_formatted})