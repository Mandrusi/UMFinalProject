from django.db import models

class Search(models.Model):
    url = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.query

class Image(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE, null=True, blank=True)
    url = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    image = models.BinaryField(editable=False, null=True)
    content_type = models.CharField(max_length=255)
    # BinaryField editable=False and null=True will make it a LONGBLOB field (4 gig max size) in Django 3.2 and above

    def __str__(self):
        return self.filename