.. -*- rst -*-

=========
Resources
=========

List Resources
==============

.. rest_method:: GET v1/{resource}

List resources of the given type.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path

Response
--------

.. rest_parameters:: parameters.yaml

  - resources: resources
  - id: resource_id
  - resource_type: resource_type
  - reservable: resource_reservable
  - data: resource_data
  - created_at: created_at
  - updated_at: updated_at
  - extra_capability: resource_extra_capability

**Example of List Resources Response**

.. literalinclude:: ../../../doc/api_samples/resources/resource-list-resp.json
  :language: javascript

Create Resource
===============

.. rest_method:: POST v1/{resource}

Create a resource.

**Response codes**

Normal response code: 201

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Conflict(409), Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - data: resource_data
  - extra_capability: resource_extra_capability

**Example of Create Resource Request**

.. literalinclude:: ../../../doc/api_samples/resources/resource-create-req.json
  :language: javascript

Response
--------

.. rest_parameters:: parameters.yaml

  - resource: resource
  - id: resource_id
  - resource_type: resource_type
  - reservable: resource_reservable
  - data: resource_data
  - created_at: created_at
  - updated_at: updated_at
  - extra_capability: resource_extra_capability

**Example of Create Resource Response**

.. literalinclude:: ../../../doc/api_samples/resources/resource-create-resp.json
  :language: javascript

Show Resource Details
=====================

.. rest_method:: GET v1/{resource}/{resource_id}

Show details of a resource.

**Preconditions**

The resource must exist.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Not Found(404), Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - resource_id: resource_id_path

Response
--------

.. rest_parameters:: parameters.yaml

  - resource: resource
  - id: resource_id
  - resource_type: resource_type
  - reservable: resource_reservable
  - data: resource_data
  - created_at: created_at
  - updated_at: updated_at
  - extra_capability: resource_extra_capability

**Example of Show Resource Details Response**

.. literalinclude:: ../../../doc/api_samples/resources/resource-details-resp.json
  :language: javascript

Update Resource
===============

.. rest_method:: PUT v1/{resource}/{resource_id}

Update a resource.

**Preconditions**

The resource must exist.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Not Found(404), Conflict(409), Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - resource_id: resource_id_path
  - extra_capability: resource_extra_capability

**Example of Update Resource Request**

.. literalinclude:: ../../../doc/api_samples/resources/resource-update-req.json
  :language: javascript

Response
--------

.. rest_parameters:: parameters.yaml

  - resource: resource
  - id: resource_id
  - resource_type: resource_type
  - reservable: resource_reservable
  - data: resource_data
  - created_at: created_at
  - updated_at: updated_at
  - extra_capability: resource_extra_capability

**Example of Resource Update Response**

.. literalinclude:: ../../../doc/api_samples/resources/resource-update-resp.json
  :language: javascript

Delete resource
===============

.. rest_method:: DELETE v1/{resource}/{resource_id}

Delete a resource.

**Preconditions**

The resource must exist.

**Response codes**

Normal response code: 204

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Not Found(404), Conflict(409), Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - resource_id: resource_id_path

Response
--------

No body content is returned on a successful DELETE.

====================
Resource Allocations
====================

List Allocations
================

.. rest_method:: GET v1/{resource}/allocations

List allocations of all resources.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - lease_id: allocation_lease_id_query
  - reservation_id: allocation_reservation_id_query

Response
--------

.. rest_parameters:: parameters.yaml

  - allocations: allocations
  - resource_id: resource_id
  - reservations: reservation_allocation
  - reservation.id: reservation_id
  - reservation.lease_id: lease_id

**Example of List Allocations Response**

.. literalinclude:: ../../../doc/api_samples/resources/allocation-list-resp.json
   :language: javascript

Get Allocation
==============

.. rest_method:: GET v1/{resource}/{resource_id}/allocation

Get allocation of a resource.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Internal Server Error(500)

Request
-------

.. rest_parameters:: parameters.yaml

  - resource: resource_path
  - resource_id: resource_id_path
  - lease_id: allocation_lease_id_query
  - reservation_id: allocation_reservation_id_query

Response
--------

.. rest_parameters:: parameters.yaml

  - allocation: allocation
  - resource_id: resource_id
  - reservations: reservation_allocation
  - reservation.id: reservation_id
  - reservation.lease_id: lease_id

**Example of Get Allocation Response**

.. literalinclude:: ../../../doc/api_samples/resources/allocation-get-resp.json
   :language: javascript

List Resource Types
===================

.. rest_method:: GET v1/resources

Get available resource types.

**Response codes**

Normal response code: 200

Error response codes: Bad Request(400), Unauthorized(401), Forbidden(403),
Internal Server Error(500)

Request
-------

No body content, path, nor query option.

Response
--------

.. rest_parameters:: parameters.yaml

  - name: resources_name
  - prefix: resources_prefix

**Example of Get Allocation Response**

.. literalinclude:: ../../../doc/api_samples/resources/resources.json
   :language: javascript
