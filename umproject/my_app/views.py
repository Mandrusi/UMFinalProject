import requests # Handles http requests
import base64 # base64 encoding for sending BLOBs to template as text
import sys # for debug flushes
from io import BytesIO # Handle binary data to save img_data to database
from .models import Image, Search # Search and Image models (objects for database)
# from PIL import Image as PILImage # For raster based image manipulation
from django.shortcuts import render, redirect # For rendering templates with context data and returning HTTP responses
from bs4 import BeautifulSoup # For parsing html content
from urllib.parse import urljoin, urlparse # For combining relative references to full URL
from django.http import HttpResponse # For determining HttpResponse types
from django.utils import timezone # For displaying timezone

# potential addition in longterm development
# add a numeric value to debugs indicating criticality e.g. (1 for most critical, 5 for trivial)
def debug(str):
    print(f"{str}")
    sys.stdout.flush()
    return

# Handler function to get_web_response for index view
def get_web_response_handler(request, url_with_scheme, url_entered):
    # Get HTML content of the URL, but handle redirects manually, to update url to new location
    try:
        response = requests.get(url_with_scheme, allow_redirects=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        debug("Error trying to get {url_with_scheme}: {e}")
        return render(request, 'fail.html',{'error_message': f"Failed to get {url_with_scheme}"})

    debug("in index(), returned from requests.get with response={response}")

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
            debug("Error trying to redirect to {response.headers['Location']}: {e}")
            return render(request, 'fail.html',{'error_message': f"Failed to redirect from {url_entered}, to {url}"})

        if response.status_code in {200,201}: # if redirected get() status ok or created (kinda ok)
            debug("Successfully redirected to {url}")
        else:
            debug("Failed redirect to get {url}, status code {response.status_code}")
            return render(request, 'fail.html',{'error_message': f"Failed redirect to get {url_entered} HttpResponse:{response.status_code}"})
    else:
        debug("Status code {response.status_code} trying to get {url_entered}")
        return render(request, 'fail.html',{'error_message': f"Could not get {url_entered} HttpResponse:{response.status_code}"})
    return(url, response)

def src_set_handler(img):
    # an img srcset in html will list URLs of the same image in different sizes,
    # separated by commas, to allow picking the best size for a layout.
    # if we pick an image that's many megabytes, it chokes saving to the database.
    # so we cap the size at an area of 100,000 pixels, and pick the biggest url in the 
    # set of images that's under 100,000. if there are none we skip this image.
    # it can be slow checking a lot of image sizes...if speed was more crucial, maybe
    # check just the first and last urls, as they're probably in ascending or descending
    # order by size, so one or the other might fit our criteria. 

    maximum_size_allowed = 100000    
    if 'srcset' in img.attrs:
        srcset = img['srcset']
    elif 'data-srcset' in img.attrs:
        srcset = img['data-srcset']
    sizes = srcset.split(',')
    biggest_size = None
    biggest_url = None
    for size in sizes:
        # Get the image URL and size

# Could use some error checking on this request 
        parts = size.strip().split()
        url_to_check = parts[0]
        response = requests.get(url_to_check)
        image_size_bytes = len(response.content)


        debug(".....Checking url={url_to_check} size={image_size_bytes}")
        # Update the biggest area and URL if necessary
        if image_size_bytes <= maximum_size_allowed and (biggest_size is None or image_size_bytes > biggest_size):
            biggest_size = image_size_bytes
            biggest_url = url_to_check

    if biggest_url is None:
        debug("none of the images were suitable")
        return

    img['src'] = biggest_url
    img_url = biggest_url
    debug("chose best size from srcset, img_url = {img_url}, size={biggest_size}")
    return img_url

def retrieve_and_validate_img_handler(img_url):
# Retrieve the image data from the URL
    try:
        response = requests.get(img_url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        debug("Error retrieving image {img_url}: {e}")
        return
    # Don't store images in database if over max size, currently 1024576.
    # It can handle bigger, but may affect performance at some level,
    # and that seems like a reasonable limit.
    maximium_size_to_save = 1024576

    image_size_bytes = len(response.content)
    if image_size_bytes > maximium_size_to_save:
        return

    # Get image type based on file extension
    content_type = response.headers.get('content-type')
    if not content_type.startswith("image/"): # if not an image type of content, skip it
    #    debug("Invalid image content type for {img_url}: {content_type}")
        return # Not image content_type (maybe "text/html") so skip to next loop iterator

    debug("content_type: {content_type}")
    return response, content_type

def database_save_handler(response, search, img_url, content_type):
    # Save the Image object to the database
    try:
        img_data = BytesIO(response.content)
    except OSError as e:
        debug("Error setting img_data to BytesIO() for {img_url}: {e}")
        return

    debug("got img_data from BytesIO, img_data={img_data}")

    img_obj = Image(search=search, url=img_url, image=img_data.getvalue(), content_type=content_type) 
    debug("did Image() call") 
    img_obj.save()
    debug("did img_obj.save")
    return True

def index(request):
    debug("starting index(request), request=request")

    if request.method == 'POST':

        url_entered = request.POST['url'] # request the url entered by the user
        parsed_url = urlparse(url_entered) # Parse the URL entered by the user to get the scheme
        scheme = parsed_url.scheme if parsed_url.scheme else 'http' # Figure out scheme (e.g. http)
        url_with_scheme = f"{scheme}://{parsed_url.netloc}{parsed_url.path}" # Rebuild URL
        
        url, response = get_web_response_handler(request,url_with_scheme, url_entered) # Call handler function for getting web response
        debug("in get_web_response = {url}, {response}")

        soup = BeautifulSoup(response.content, 'html.parser')  # Parse HTML content with BeautifulSoup
        img_tags = soup.find_all('img') # Extract all the image URLs from the HTML content
        search = Search.objects.create(url=url_entered) # Save the search instance

        for img in img_tags:
            if 'src' in img.attrs and img['src'] != '': # this is a simple <img src=...> tag
                img_url = img['src']
                debug("in img_tags loop, img_url = {img_url}")
            elif 'srcset' in img.attrs or 'data-srcset' in img.attrs:
                img_url = src_set_handler(img) # Call handler function for srcset
                if img_url is None:
                    continue
            else:
                debug("skipping img in img_tags loop")
                continue  # Skip this image tag since it has no 'src' or 'srcset' attributes

            if not img_url.startswith('http'):
                img_url = urljoin(url, img_url)
            
            response, content_type = retrieve_and_validate_img_handler(img_url) # call handler function for retrieving and validating img
            if response is None:
                continue
            
            database_save_handler(response, search, img_url, content_type) # call handler function for saving objects to database
            

        return redirect('success', id=search.id)
    return render(request, 'index.html')

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
#        image.image_format = "image/jpeg"

def past_searches(request):
    # Get all past searches, format local timestamp, and send to template
    searches = Search.objects.all()
    for search in searches:
        local_tz = timezone.get_current_timezone()
        local_dt = search.timestamp.astimezone(local_tz)
        search.timestampadjuster = local_dt.strftime('%Y-%m-%d %H:%M:%S')
    return render(request, 'past_searches.html', {'searches': searches},) # Render list of searches to template
    
def past_search(request):
    try:
        # Check if an id parameter was sent (e.g. http://127.0.0.1:8000/past_searches?id=5) 
        search_id_for_page = request.GET['id']
    except KeyError:
        # No id sent to this as an attribute to the URL
        return render(request, 'fail.html', {'error_message': "No ID sent to past_search.html"})
    
    # An id was sent, so send all the images for that search id to past_search.html
#    searches = Search.objects.filter(id=search_id_for_page)
 #   for search in searches:
  #      search_url = search.url

    try:
        search = Search.objects.get(id=search_id_for_page)
        
    except Search.DoesNotExist:
        return render(request, 'fail.html',{'error_message': "Search ID not found"})
    
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
        image.filename = filename
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(img_data).decode('utf-8')}"
    debug("returning past_search.html")
    # return render(request, 'past_search.html', {'images': images}, {'search_url': search_url})
    return render(request, 'past_search.html', {'images': images, 'search_url': search.url, 'search_timestamp': search.timestamp_local})

def success(request, id):
    debug("in success(), id={id} ")

    try:
        search = Search.objects.get(id=id)
        
    except Search.DoesNotExist:
        return render(request, 'fail.html', {'error_message': "Unexpected problem retrieving images from search"},)
    
    local_tz = timezone.get_current_timezone()
    local_dt = search.timestamp.astimezone(local_tz)
    search.timestamp_local = local_dt.strftime('%Y-%m-%d %H:%M:%S')

    images = Image.objects.filter(search_id=id)

    for image in images:
        img_data = image.image
        # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
        filename = image.url.split('?')[0]
        # split remaining url by / and pick the last (-1) element, which is just the filename
        filename = filename.split('/')[-1]
        image.filename = filename
        image.image_data_uri = f"data:{image.content_type};base64,{base64.b64encode(img_data).decode('utf-8')}"

    return render(request, 'success.html', {'images': images, 'search_url': search.url, 'search_timestamp': search.timestamp_local})