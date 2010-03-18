# -*- coding: utf-8 -*-

from __future__ import with_statement

import threading
import neo4j

__all__ = 'Product', 'Category', 'SubCategories', 'Attribute', #'AttributeType',


class Product(object): # instance of Category

    def __init__(self, graphdb, node):
        self.__node = node
        #self.__graphdb = graphdb
    #graphdb = property(lambda self: self.__graphdb)

    def __new__(Product, graphdb, node):
        """Create a Product representation from a Node"""
        # 1. Lookup the category 
        # 2. Create the product as an instance of the category
        return Category(graphdb, node.PRODUCT.single.start)(graphdb, node)

    global product_node # define here to get the name mangling right
    def product_node(self):
        return self.__node


class Category(type): # type of Product
    __categories = {}
    __create_lock = threading.RLock() # reentrant lock

    graphdb = property(lambda self: self.__graphdb)

    def __new__(Category, graphdb, node):
        """Lookup or create a Category representaton for a Node."""
        # If the Category instance already exists
        self = Category.__categories.get(node.id)
        if self is None: # Otherwise create it
            with Category.__create_lock: # Unless it was created concurrently
                self = Category.__categories.get(node.id)
                if self is not None: return self

                with graphdb.transaction:

                    # Get the name of the category
                    name = node['Name']
                    if isinstance(name, unicode):
                        name = name.encode('ascii')

                    # Get the attributes for products in the category
                    attributes = dict(__new__=object.__new__)
                    for attr in node.ATTRIBUTE:
                        # Get the Attribute type (instance of AttributeType)
                        Attribute = AttributeType( graphdb, attr.end )
                        # Instanciate the attribute
                        attribute = Attribute( graphdb, attr['Name'],
                                               attr.get('DefaultValue') )
                        # Add the attribute to the category instance dict
                        attributes[ attr['Name'] ] = attribute

                    # Get the parent category (the superclass of this category)
                    parent = node.SUBCATEGORY.single
                    if parent is None:
                        parent = Product # The base Category type
                    else:
                        parent = Category(graphdb, parent.start)
                    
                    # Create a new type instance representing the Category
                    self = type.__new__(Category, name, (parent,), attributes)
                    self.__graphdb = graphdb
                    self.__node = node
                    Category.__categories[node.id] = self

        return self

    global category_node # define here to get the name mangling right
    def category_node(self):
        return self.__node

    @neo4j.transactional(graphdb)
    @property
    def name(self):
        return self.__node['Name']

    @property
    def parent(self):
        try:
            return Category(self.graphdb,
                            self.__node.SUBCATEGORY.incoming.single.start)
        except:
            return self

    def new_subcategory(self, name, **attributes):
        """Create a new sub category"""
        with self.__create_lock:
            with self.graphdb.transaction:
                node = self.graphdb.node(Name=name)
                self.__node.SUBCATEGORY(node)
                for key, factory in attributes.items():
                    factory(node, key)
                return Category(self.graphdb, node)

    def new_product(self, **values):
        """Create a new product in this category"""
        with self.graphdb.transaction:
            node = self.graphdb.node()
            self.__node.PRODUCT(node)
            product = self(graphdb, node)
            for key, value in values.items():
                setattr(product, key, value)
            return product

    def __iter__(self):
        """Iterating over a category yeilds all its products.
        This includes products in subcategories of this category."""
        for prod in SubCategoryProducts(self.__node):
            yield Product(self.graphdb, prod)

    @property
    def categories(self):
        for rel in self.__node.SUBCATEGORY.outgoing:
            yield Category(self.graphdb, rel.end)


class SubCategoryProducts(neo4j.Traversal):
    "Traverser that yields all products in a category and its sub categories."
    types = [neo4j.Outgoing.SUBCATEGORY, neo4j.Outgoing.PRODUCT]
    def isReturnable(self, pos):
        if pos.is_start: return False
        return pos.last_relationship.type == 'PRODUCT'


class SubCategories(neo4j.Traversal):
    "Traverser that yields all subcategories of a category."
    types = [neo4j.Outgoing.SUBCATEGORY]
    


class AttributeType(type): # type of Attribute
    __attribute_types = {}
    __create_lock = threading.RLock() # reentrant lock

    def __new__(AttributeType, graphdb, node):
        """Lookup or create a AttributeType representaton for a Node."""
        # If the AttributeType instance already exists
        self = AttributeType.__attribute_types.get(node.id)
        if self is None: # Otherwise create it
            with AttributeType.__create_lock: # Unless created concurrently
                self = AttributeType.__attribute_types.get(node.id)
                if self is not None: return self

                with graphdb.transaction:

                    body = dict(__new__=object.__new__)
                    self = type.__new__(AttributeType, node['Name'],
                                        (Attribute,), body)

                    self.__node = node

        return self

    @classmethod
    def create(AttributeType, graphdb, root, name, **attributes):
        unit = attributes.get('Unit', "")
        with graphdb.transaction:

            node = graphdb.node(Name=name, Unit=unit)
            root.ATTRIBUTE_TYPE(node)
            
            for rel in root.ATTRIBUTE_TYPE:
                if rel.end == node: continue
                if rel.end['Name'] == name:
                    raise KeyError("AttributeType %r already exists" % (name,))

            return AttributeType(graphdb, node)

    @property
    def unit(self):
        return self.__node.get('Unit', '')

    @property
    def name(self):
        return self.__node['Name']

    global type_node
    def type_node(self):
        return self.__node

    def to_primitive_neo_value(self, value):
        return value

    def from_primitive_neo_value(self, value):
        return value


class Attribute(object): # instance of AttributeType

    def __new__(self, type, **kwargs):
        required = kwargs.pop('required', 'default' in kwargs)
        default = kwargs.pop('default', None)
        if kwargs: raise TypeError("Unsupported keyword arguments: "+", ".join(
                "'%s'" % (key,) for key in kwargs))
        def AttributeFactory(node, name):
            attr=node.ATTRIBUTE( type_node(type), Name=name, Required=required)
            if default is not None:
                attr['DefaultValue'] = type.to_primitive_neo_value(default)
        return AttributeFactory

    def __init__(self, graphdb, key, default):
        self.key = key
        self.default = default

    def __str__(self):
        return '<Attribute type=%s Name=%r DefaultValue=%r>' % (
            self.__class__.__name__, self.key, self.default)

    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        else:
            return self.from_neo(product_node(obj).get(self.key, self.default))

    def __set__(self, obj, value):
        node(obj)[self.key] = self.to_neo(value)

    def __delete__(self, obj):
        del node(obj)[self.key]

    @classmethod
    def to_neo(Attribute, value): # Delegate to the AttributeType
        return Attribute.to_primitive_neo_value(value)

    @classmethod
    def from_neo(Attribute, value): # Delegate to the AttributeType
        return Attribugte.from_primitive_neo_value(value)
