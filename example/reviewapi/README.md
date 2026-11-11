# to-run
```
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
# create data
use the Django admin interface (http://127.0.0.1:8000/admin/) to create categories, products, and reviews.

# to generate drf-spectacular schema
```
./manage.py spectacular --color --file schema.yml
```

# notes
- this is the code for the server for the same example in the journey of a request section of the paper.
- we see that even in this simple example, the tool could not deduce the type of the category id (say in the following endpoint /api/v2/categories/{category_pk}/products/{product_pk}/reviews/) because we used a custom nested router package. it is a very popular package even mentioned in the official docs. although the tool threw a warning for the same. this is the case where the tool is aware of its lack of information. this would help the developer to manually provide the info using the the drf decorator. we might want to point out cases where the tool does not even throw a warning.
