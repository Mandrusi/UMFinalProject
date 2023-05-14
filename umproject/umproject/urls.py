"""
URL configuration for umproject.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
# imported views
from my_app import views

urlpatterns = [
    path('admin/', admin.site.urls),
    # configured the url
    path('',views.index, name="homepage"),
    path('show_all_images/', views.show_all_images, name='show_all_images'),
    path('success/<int:id>/', views.success, name='success'),
    path('past_searches/', views.past_searches, name='past_searches'),
    path('past_search.html', views.past_search, name='past_search'),
]