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
      # BinaryField editable=False and null=True will make it a LONGBLOB field (4 gig max size) in Django 3.2 and above
    content_type = models.CharField(max_length=64)
    unique_search_image = models.CharField(max_length=64, unique=True, default='Whatever')
      # unique_search_image is unique to enforce a unique (search & image) in the table
      # default value is required by python migrations for reasons that sound dubious 

    # This attempt at constraining search & image field to combined uniqueness also failed
    #
    # Constraint should throw ValidationError exception if we
    # try to insert the same 'image' blob for a given 'search' ForeignKey
    #
    # def validate_unique(self, *args, **kwargs):
    #     super().validate_unique(*args, **kwargs)
    #     if self.__class__.objects.filter(search=self.search, image=self.image).exists():
    #         raise ValidationError(
    #             message='MyModel with this (search, image) already exists.',
    #             code='unique_together',
    #         )
        
    # This attempt at constraining search & image field to combined uniqueness failed
    #
    # class Meta:
    #     constraints = [
    #         # Unique constraint should throw  django.db.IntegrityError exception if we
    #         # try to insert the same blob for a given search_id
    #         models.UniqueConstraint(fields=['search', 'image'], name='unique_search_image')
    #     ]

    def __str__(self):
        return self.filename