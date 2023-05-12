import requests
from django.shortcuts import render
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

from django.http import HttpResponse

import mimetypes
from io import BytesIO
from .models import Image, Search
from PIL import Image as PILImage

import base64
import tempfile

def index(request):
    if request.method == 'POST':
        url_entered = request.POST['url'] # request the url entered by the user
        
        # Get HTML content of the URL, but handle redirects manually, to update url to new location
        response = requests.get(url_entered, allow_redirects=False)
        if response.status_code in {200,201}: # if status ok or created (kinda ok)
            url = url_entered
        elif response.status_code in {301,302,307,308}: # temporary or permanent redirect
            url = response.headers['Location']
            response = requests.get(url)
            if response.status_code in {200,201}: # if redirected get() status ok or created (kinda ok)
                print(f"DEBUG - Successfully redirected to {url}")
            else:
                print(f"DEBUG - Failed redirect to get {url}, status code {response.status_code}")
                return render(request, 'fail.html')
        else:
            print(f"DEBUG - Status code {response.status_code} trying to get {url}")
            return render(request, 'fail.html')

        soup = BeautifulSoup(response.content, 'html.parser')  # Parse HTML content with BeautifulSoup
        img_tags = soup.find_all('img') # Extract all the image URLs from the HTML content
        search = Search.objects.create(url=url_entered) # Save the search instance

        for img in img_tags:
            if 'src' in img.attrs and img['src'] != '': # this is a simple <img src=...> tag
                img_url = img['src']
                print(f"DEBUG in img_tags loop, img_url = {img_url}")
            elif 'srcset' in img.attrs or 'data-srcset' in img.attrs:

                # an img srcset in html will list URLs of the same image in different sizes,
                # separated by commas, to allow picking the best size for a layout.
                # if we pick an image that's many megabytes, it chokes saving to the database.
                # so we cap the size at an area of 50,000 pixels, and pick the biggest url in the 
                # set of images that's under 50,000. if there are none we skip this image.
                # it can be slow checking a lot of image sizes...if speed was more crucial, maybe
                # check just the first and last urls, as they're probably in ascending or descending
                # order by size, so one or the other might fit our criteria. 

                maximum_size_allowed = 50000    
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


                    print(f"DEBUG.....Checking url={url_to_check} size={image_size_bytes}")
                    # Update the biggest area and URL if necessary
                    if image_size_bytes <= maximum_size_allowed and (biggest_size is None or image_size_bytes > biggest_size):
                        biggest_size = image_size_bytes
                        biggest_url = url_to_check

                if biggest_url is None:
                    print(f"DEBUG none of the images were suitable")
                    continue

                img['src'] = biggest_url
                img_url = biggest_url

                print(f"DEBUG - chose best size from srcset, img_url = {img_url}, size={biggest_size}")
            else:
                print(f"DEBUG - skipping img in img_tags loop")
                continue  # Skip this image tag since it has no 'src' or 'srcset' attributes

            if not img_url.startswith('http'):
                img_url = urljoin(url, img_url)
           
            parsed_url = urlparse(img_url)
            # sometimes the url contains ? and other extraneous data after the filename, so strip everything after ?
            filename = img_url.split('?')[0]
            # split remaining url by / and pick the last (-1) element, which is just the filename
            filename = filename.split('/')[-1]
            
            # Retrieve the image data from the URL
            try:
                response = requests.get(img_url)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"Error retrieving image {img_url}: {e}")
                continue

            # Get image type based on file extension
            content_type = response.headers.get('content-type')
            if not content_type.startswith("image/"): # if not an image type of content, skip it
            #    print(f"Invalid image content type for {img_url}: {content_type}")
                continue # Not image content_type (maybe "text/html") so skip to next loop iterator
    
            print(f"DEBUG - content_type: {content_type}")

                
            # Save the Image object to the database
            try:
                img_data = BytesIO(response.content)
            except OSError as e:
                print(f"DEBUG - Error setting img_data to BytesIO() for {img_url}: {e}")
                continue

            print(f"DEBUG - got img_data from BytesIO, img_data={img_data}")

            img_obj = Image(search=search, url=img_url, filename=filename, image=img_data.getvalue()) 
            print(f"DEBUG - did Image() call") 
            img_obj.save()
            print(f"DEBUG - did img_obj.save")

        return render(request, 'success.html')
    return render(request, 'index.html')

def myimage(request, image_id):
    image = Image.objects.get(pk=image_id)
    return HttpResponse(image.image, content_type="image/jpeg")

def image_list(request):
    images = Image.objects.all()
    for image in images:
        img_data = image.image
       
        #image.image_format = "image/svg+xml"

        content_type, _ = mimetypes.guess_type(image.filename)
        if content_type is None:
            if img_data.startswith(b'GIF89a'):
                content_type = "image/gif"
            elif img_data[6:10] == b'JFIF':
                content_type = "image/jpeg"
            else: 
                content_type = "application/octet-stream"
                print(f"DEBUG - filename {image.filename} app/octet-stream img_data = {img_data[1:100]}")
        image.image_format = content_type

        image.image_data_uri = f"data:{image.image_format};base64,{base64.b64encode(img_data).decode('utf-8')}"
    return render(request, 'image_list.html', {'images': images})
#        image.image_format = "image/jpeg"

def success(request):
    return render(request, 'success.html')