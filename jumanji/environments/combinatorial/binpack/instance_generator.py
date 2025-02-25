# Copyright 2022 InstaDeep Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import collections
import csv
import functools
import operator
from typing import List, Tuple

import chex
import jax
import jax.numpy as jnp
from chex import Array, PRNGKey

from jumanji.environments.combinatorial.binpack.space import Space
from jumanji.environments.combinatorial.binpack.types import (
    Container,
    Item,
    Location,
    State,
    empty_ems,
    item_from_space,
    location_from_space,
)
from jumanji.tree_utils import tree_slice, tree_transpose

# 20 foot container: 5.898m long x 2.352m wide x 2.393m high
# Oftentimes people use different values (inner volume) : 5.870m long x 2.330m wide x 2.200m high
# real_container_volume = 1.103 * inner_container_volume
TWENTY_FOOT_DIMS = (5870, 2330, 2200)

CSV_COLUMNS = ["Product_Name", "Length", "Width", "Height", "Quantity"]


def make_container(container_dims: Tuple[int, int, int]) -> Container:
    """Return a non-normalized `Container` whose dimensions are in millimeters.

    Args:
        container_dims: (length, width, height) tuple of integers corresponding to the dimensions
            of the container in millimeters.

    Returns:
        unnormalized container whose bottom-left corner is (0, 0, 0) and top-right corner is
            defined by container_dims.

    """
    x2, y2, z2 = container_dims
    container = Container(x1=0, x2=x2, y1=0, y2=y2, z1=0, z2=z2).astype(jnp.int32)
    return container


class InstanceGenerator(abc.ABC):
    """Defines the abstract `InstanceGenerator` base class. An `InstanceGenerator` is responsible
    for generating an instance when the environment is reset.
    """

    def __init__(
        self, max_num_items: int, max_num_ems: int, container_dims: Tuple[int, int, int]
    ):
        """Abstract class implementing `max_num_items` and `max_num_ems` properties.

        Args:
            max_num_items: maximum number of items the generator will ever generate when creating
                a new instance. This defines the shapes of arrays related to items in the
                environment state.
            max_num_ems: maximum number of ems the environment will handle. This defines the shape
                of the EMS buffer that is kept in the environment state.
        """
        self._max_num_items = max_num_items
        self._max_num_ems = max_num_ems
        self._container_dims = container_dims

    @property
    def max_num_items(self) -> int:
        """Returns the maximum number of items the generator will ever create when resetting the
        environment."""
        return self._max_num_items

    @property
    def max_num_ems(self) -> int:
        """Returns the maximum number of ems the environment will handle. Any created ems that do
        not fit in the buffer will be ignored during the environment step."""
        return self._max_num_ems

    @property
    def container_dims(self) -> Tuple[int, int, int]:
        """Returns the dimensions of the container generated by the instance generator."""
        return self._container_dims

    @abc.abstractmethod
    def __call__(self, key: PRNGKey) -> State:
        """Call method responsible for generating a new state.

        Args:
            key: jax random key for any stochasticity used in the instance generation process.

        Returns:
            A BinPack State.
        """

    def generate_solution(self, key: PRNGKey) -> State:
        """Returns a state in which all items are placed to show a solution to the packing problem.
        The state corresponds to the end of an episode run with the optimal policy.

        Args:
            key: same jax random key used than when calling `__call__` to generate the same
                instance.

        Returns:
            A BinPack state whose items are all placed.
        """
        raise NotImplementedError

    def _unpack_items(self, state: State) -> State:
        """Unpack any items that were packed in the given state. Reset to how the state was at the
        beginning of the episode.
        """
        state.ems_mask = jnp.zeros(self.max_num_ems, bool).at[0].set(True)
        state.items_placed = jnp.zeros(self.max_num_items, bool)
        state.items_location = Location(
            x=jnp.zeros(self.max_num_items, jnp.int32),
            y=jnp.zeros(self.max_num_items, jnp.int32),
            z=jnp.zeros(self.max_num_items, jnp.int32),
        )
        return state


class ToyInstanceGenerator(InstanceGenerator):
    """`InstanceGenerator` that can be used as an example. It deterministically outputs a single
    instance with 20 items that can be packed in a way to fully utilize a 20-ft container.
    """

    def __init__(self) -> None:
        """Instantiate a ToyInstanceGenerator with 20 items and 80 EMS maximum."""
        super().__init__(
            max_num_items=20, max_num_ems=80, container_dims=TWENTY_FOOT_DIMS
        )

    def __call__(self, key: PRNGKey) -> State:
        """Call method responsible for generating a new state. It returns a 20-ft container instance
        without any placed items.

        Args:
            key: jax random key for any stochasticity used in the generation process

        Returns:
            A BinPack State.
        """
        del key

        solution = self._generate_solved_instance()
        state = self._unpack_items(solution)

        return state

    def generate_solution(self, key: PRNGKey) -> State:
        """Returns a state in which all items are placed to show a solution to the packing problem.
        The state corresponds to the end of an episode that was run with the optimal policy.

        Args:
            key: same jax random key used than when calling `__call__` to generate the same
                instance.

        Returns:
            A BinPack state whose items are all placed.

        Example:
            ```python
            instance_generator = ToyInstanceGenerator()
            env = BinPack(instance_generator, obs_num_ems=40)
            key = jax.random.key(0)
            reset_state = instance_generator(key)
            env.render(reset_state)
            solution = instance_generator.generate_solution(key)
            env.render(solution)
            ```
        """
        del key
        solution = self._generate_solved_instance()
        return solution

    def _generate_solved_instance(self) -> State:
        """Generate the 20-ft instance with all items correctly packed."""
        container = make_container(self.container_dims)

        list_of_ems = [container] + [empty_ems() for _ in range(self.max_num_ems - 1)]
        ems = tree_transpose(list_of_ems)
        ems_mask = jnp.zeros(self.max_num_ems, bool)

        items = Item(
            x_len=jnp.array(
                [
                    2445,
                    3083,
                    1950,
                    3425,
                    3083,
                    2787,
                    3425,
                    2787,
                    2787,
                    3425,
                    3083,
                    2787,
                    1295,
                    2787,
                    3083,
                    3425,
                    3425,
                    837,
                    1150,
                    3425,
                ],
                jnp.int32,
            ),
            y_len=jnp.array(
                [
                    1306,
                    1429,
                    1301,
                    321,
                    1165,
                    2330,
                    291,
                    1504,
                    826,
                    466,
                    901,
                    1029,
                    1024,
                    2330,
                    1165,
                    466,
                    663,
                    1301,
                    1024,
                    589,
                ],
                jnp.int32,
            ),
            z_len=jnp.array(
                [
                    1022,
                    549,
                    700,
                    1022,
                    629,
                    200,
                    1022,
                    157,
                    157,
                    363,
                    549,
                    700,
                    1022,
                    121,
                    629,
                    659,
                    1022,
                    700,
                    1022,
                    1022,
                ],
                jnp.int32,
            ),
        )
        items_mask = jnp.ones(self.max_num_items, bool)

        sorted_ems_indexes = jnp.arange(0, self.max_num_ems, dtype=int)

        all_item_locations = Location(
            x=jnp.array(
                [
                    0,
                    0,
                    3083,
                    2445,
                    0,
                    3083,
                    2445,
                    3083,
                    3083,
                    2445,
                    0,
                    3083,
                    0,
                    3083,
                    0,
                    2445,
                    2445,
                    5033,
                    1295,
                    2445,
                ],
                jnp.int32,
            ),
            y=jnp.array(
                [
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    910,
                    0,
                    1504,
                    1864,
                    1429,
                    1301,
                    1306,
                    0,
                    1165,
                    1864,
                    1201,
                    0,
                    1306,
                    321,
                ],
                jnp.int32,
            ),
            z=jnp.array(
                [
                    0,
                    1022,
                    1022,
                    0,
                    1571,
                    1722,
                    0,
                    2043,
                    2043,
                    0,
                    1022,
                    1022,
                    0,
                    1922,
                    1571,
                    363,
                    0,
                    1022,
                    0,
                    0,
                ],
                jnp.int32,
            ),
        )
        solution = State(
            container=container,
            ems=ems,
            ems_mask=ems_mask,
            items=items,
            items_mask=items_mask,
            items_placed=items_mask,
            items_location=all_item_locations,
            action_mask=None,
            sorted_ems_indexes=sorted_ems_indexes,
            # For deterministic instance generators we always set the key to 0.
            key=jax.random.PRNGKey(0),
        )

        return solution


class CSVInstanceGenerator(InstanceGenerator):
    """`InstanceGenerator` that parses a CSV file to do active search on a single instance. It
    always resets to the same instance defined by the CSV file. The generator can handle any
    container dimensions but assumes a 20-ft container by default.

    The CSV file is expected to have the following columns:
    - Product_Name
    - Length
    - Width
    - Height
    - Quantity

    Example of such a CSV file:
        Product_Name,Length,Width,Height,Quantity
        shape_1,1080,760,300,5
        shape_2,1100,430,250,3
    """

    def __init__(
        self,
        csv_path: str,
        max_num_ems: int = 150,
        container_dims: Tuple[int, int, int] = TWENTY_FOOT_DIMS,
    ):
        """Instantiate a CSVInstanceGenerator that generates the same instance (active search)
        defined by a CSV file.

        Args:
            csv_path: path to the CSV file defining the instance to reset to.
            max_num_ems: maximum number of ems the environment will handle. This defines the shape
                of the EMS buffer that is kept in the environment state. Default to 150, but the
                good number heavily depends on the number of items (given by the CSV file).
            container_dims: (length, width, height) tuple of integers corresponding to the
                dimensions of the container in millimeters. By default, assume a 20-ft container.
        """
        self.instance_from_csv = self._parse_csv_file(
            csv_path, max_num_ems, container_dims
        )
        max_num_items = self.instance_from_csv.items_mask.shape[0]
        super().__init__(max_num_items, max_num_ems, container_dims)

    def __call__(self, key: PRNGKey) -> State:
        """Call method responsible for generating a new state.

        Args:
            key: key is not used as the instance is deterministically generated from CSV file.

        Returns:
            A BinPack State that corresponds to the instance defined by the CSV file.
        """
        del key
        return self.instance_from_csv

    def _parse_csv_file(
        self, csv_path: str, max_num_ems: int, container_dims: Tuple[int, int, int]
    ) -> State:
        """Create an instance by parsing a CSV file.

        Args:
            csv_path: path to the CSV file to parse that defines the instance to reset to.
            max_num_ems: maximum number of ems the environment will handle. This defines the shape
                of the EMS buffer that is kept in the environment state.
            container_dims: (length, width, height) tuple of integers corresponding to the
                dimensions of the container in millimeters. By default, assume a 20-ft container.

        Returns:
            BinPack state that contains the instance defined in the CSV file.
        """
        container = make_container(container_dims)

        # Initialize the EMS
        list_of_ems = [container] + (max_num_ems - 1) * [empty_ems()]
        ems = tree_transpose(list_of_ems)
        ems_mask = jnp.zeros(max_num_ems, bool).at[0].set(True)

        # Parse the CSV file to generate the items
        rows = self._read_csv(csv_path)
        list_of_items = self._generate_list_of_items(rows)
        items = tree_transpose(list_of_items)

        # Initialize items mask and location
        num_items = len(list_of_items)
        items_mask = jnp.ones(num_items, bool)
        items_placed = jnp.zeros(num_items, bool)
        items_location = Location(
            x=jnp.zeros(num_items, jnp.int32),
            y=jnp.zeros(num_items, jnp.int32),
            z=jnp.zeros(num_items, jnp.int32),
        )

        sorted_ems_indexes = jnp.arange(0, max_num_ems, dtype=int)
        reset_state = State(
            container=container,
            ems=ems,
            ems_mask=ems_mask,
            items=items,
            items_mask=items_mask,
            items_placed=items_placed,
            items_location=items_location,
            action_mask=None,
            sorted_ems_indexes=sorted_ems_indexes,
            # For deterministic instance generators we always set the key to 0.
            key=jax.random.PRNGKey(0),
        )

        return reset_state

    def _read_csv(self, csv_path: str) -> List[Tuple[str, int, int, int, int]]:
        rows = []
        with open(csv_path, newline="") as csvfile:
            reader = csv.reader(csvfile)
            for row_index, row in enumerate(reader):
                if row_index == 0:
                    if len(row) != len(CSV_COLUMNS):
                        raise ValueError(
                            f"Got wrong number of columns, expected: {', '.join(CSV_COLUMNS)}"
                        )
                    elif row != CSV_COLUMNS:
                        raise ValueError("Columns in wrong order")
                else:
                    # Column order: Product_Name, Length, Width, Height, Quantity.
                    rows.append(
                        (
                            row[0],
                            int(row[1]),
                            int(row[2]),
                            int(row[3]),
                            int(row[4]),
                        )
                    )
        return rows

    def _generate_list_of_items(
        self, rows: List[Tuple[str, int, int, int, int]]
    ) -> List[Item]:
        """Generate the list of items from a Pandas DataFrame.

        Args:
            rows: List[tuple] describing the items for the corresponding instance.

        Returns:
            List of Item flattened so that identical items (quantity > 1) are copied according to
                their quantity.
        """
        list_of_items = []
        for (_, x_len, y_len, z_len, quantity) in rows:
            identical_items = quantity * [
                Item(
                    x_len=jnp.array(x_len, jnp.int32),
                    y_len=jnp.array(y_len, jnp.int32),
                    z_len=jnp.array(z_len, jnp.int32),
                )
            ]
            list_of_items.extend(identical_items)
        return list_of_items


def save_instance_to_csv(state: State, path: str) -> None:
    """Save an instance to a CSV file. The conversion to CSV will save item dimensions
    in millimeters.

    Args:
        state: BinPack State.
        path: path to save the CSV file.

    Returns:
        None

    Example:
        Product_Name,Length,Width,Height,Quantity
        shape_1,1080,760,300,5
        shape_2,1100,430,250,3
    """
    items = list(zip(state.items.x_len, state.items.y_len, state.items.z_len))
    items = [
        tuple(x.item() for x in item)
        for item, mask in zip(items, state.items_mask)
        if mask and all(x > 0 for x in item)
    ]
    grouped_items = list(collections.Counter(items).items())
    grouped_items.sort(key=operator.itemgetter(1), reverse=True)
    rows = [
        (f"shape_{i}", *item, count)
        for i, (item, count) in enumerate(grouped_items, start=1)
    ]
    with open(path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)


class RandomInstanceGenerator(InstanceGenerator):
    """Instance generator that generates random instances by splitting a container into different
    items in a random fashion. The generation works as follows. At each step, a space inside the
    container is chosen and split either into 2 items of random size or into more than 2 items of
    the same size, all becoming new spaces to potentially split. It starts with the whole container
    as unique space and then ends when enough items are created, i.e., when
    `num_items >= max_num_items - _split_num_same_items + 1`.

    Example:
        ```python
        instance_generator = RandomInstanceGenerator()
        env = BinPack(instance_generator)
        key = jax.random.key(0)
        reset_state = instance_generator(key)
        env.render(reset_state)
        solution = instance_generator.generate_solution(key)
        env.render(solution)
        ```"""

    def __init__(
        self,
        max_num_items: int = 40,
        max_num_ems: int = 200,
        split_eps: float = 0.3,
        prob_split_one_item: float = 0.7,
        split_num_same_items: int = 3,
        container_dims: Tuple[int, int, int] = TWENTY_FOOT_DIMS,
    ):
        """Instantiate a RandomInstanceGenerator.

        Args:
            max_num_items: maximum number of items the generator will ever generate when creating
                a new instance. This defines the shapes of arrays related to items in the
                environment state. Default to 40, but the more items the more difficult the
                environment will be.
            max_num_ems: maximum number of ems the environment will handle. This defines the shape
                of the EMS buffer that is kept in the environment state. Default to 200, but the
                good number heavily depends on the number of items (given by `max_num_items`).
            split_eps: fraction of edges of a space that cannot be chosen as a split point. This
                prevents from infinitely small items and biases the distribution towards
                reasonable-size items.
            prob_split_one_item: probability of splitting a space into 2 non-equal spaces.
                Otherwise, the split is done into multiple copies of the same divided space.
            split_num_same_items: if a space is split into multiple spaces (probability
                `1 - split_one_item_proba`), the number of spaces to split it into is chosen
                uniformly between 1 and `split_num_same_items`.
            container_dims: (length, width, height) tuple of integers corresponding to the
                dimensions of the container in millimeters. By default, assume a 20-ft container.
        """
        super().__init__(max_num_items, max_num_ems, container_dims)
        self._split_eps = split_eps
        self._prob_split_one_item = prob_split_one_item
        self._split_num_same_items = split_num_same_items

    def __call__(self, key: PRNGKey) -> State:
        """Call method responsible for generating a new state.

        Args:
            key: jax random key for generating random splits.

        Returns:
            A BinPack State that corresponds to a random instance.
        """
        solution = self._generate_solved_instance(key)
        state = self._unpack_items(solution)

        return state

    def generate_solution(self, key: PRNGKey) -> State:
        """Returns a state in which all items are placed to show a solution to the packing problem.
        The state corresponds to the end of an episode that was run with the optimal policy.

        Args:
            key: same jax random key used than when calling `__call__` to generate the same
                instance.

        Returns:
            A BinPack state whose items are all placed.

        Example:
            ```python
            instance_generator = RandomInstanceGenerator()
            env = BinPack(instance_generator)
            key = jax.random.key(0)
            reset_state = instance_generator(key)
            env.render(reset_state)
            solution = instance_generator.generate_solution(key)
            env.render(solution)
            ```
        """
        solution = self._generate_solved_instance(key)
        return solution

    def _generate_solved_instance(self, key: PRNGKey) -> State:
        """Generate the random instance with all items correctly packed."""
        state_key, split_key = jax.random.split(key)
        container = make_container(self.container_dims)

        list_of_ems = [container] + (self.max_num_ems - 1) * [empty_ems()]
        ems = tree_transpose(list_of_ems)
        ems_mask = jnp.zeros(self.max_num_ems, bool)

        items_spaces, items_mask = self._split_container_into_items_spaces(
            container, split_key
        )
        items = item_from_space(items_spaces)
        sorted_ems_indexes = jnp.arange(0, self.max_num_ems, dtype=int)

        all_item_locations = location_from_space(items_spaces)
        solution = State(
            container=container,
            ems=ems,
            ems_mask=ems_mask,
            items=items,
            items_mask=items_mask,
            items_placed=items_mask,
            items_location=all_item_locations,
            action_mask=None,
            sorted_ems_indexes=sorted_ems_indexes,
            key=state_key,
        )
        return solution

    def _split_container_into_items_spaces(
        self, container: Container, key: PRNGKey
    ) -> Tuple[Space, Array]:
        """Split one space (the container) into several sub-spaces that will be identified as
        items.
        """
        chex.assert_rank(list(container.__dict__.values()), 0)

        def cond_fun(val: Tuple[Space, Array, PRNGKey]) -> jnp.bool_:
            _, items_mask, _ = val
            num_placed_items = jnp.sum(items_mask)
            return (
                num_placed_items < self.max_num_items - self._split_num_same_items + 1
            )

        def body_fun(val: Tuple[Space, Array, PRNGKey]) -> Tuple[Space, Array, PRNGKey]:
            items_spaces, items_mask, key = val
            key, subkey = jax.random.split(key)
            items_spaces, items_mask = self._split_space_into_sub_spaces(
                items_spaces, items_mask, subkey
            )
            next_val = items_spaces, items_mask, key
            return next_val

        items_spaces = Space(
            **jax.tree_map(
                lambda x: x * jnp.ones(self.max_num_items, jnp.int32), container
            ).__dict__
        )
        items_mask = jnp.zeros(self.max_num_items, bool).at[0].set(True)
        init_val = (items_spaces, items_mask, key)
        (items_spaces, items_mask, _) = jax.lax.while_loop(cond_fun, body_fun, init_val)

        return items_spaces, items_mask

    def _split_space_into_sub_spaces(
        self, items_spaces: Space, items_mask: Array, key: PRNGKey
    ) -> Tuple[Space, Array]:
        """Sample an axis along which to split an item."""
        chex.assert_rank([items_mask, *list(items_spaces.__dict__.values())], 1)
        axis_key, split_key = jax.random.split(key)

        # Choose a random axis to split along.
        axis = jax.random.randint(
            key=axis_key,
            shape=(),
            minval=0,
            maxval=3,
        )

        # Call `_split_along_axis` on the sampled axis: 0 -> x, 1 -> y, 2 -> z.
        items_spaces, items_mask = jax.lax.switch(
            axis,
            [
                functools.partial(self._split_along_axis, "x"),
                functools.partial(self._split_along_axis, "y"),
                functools.partial(self._split_along_axis, "z"),
            ],
            items_spaces,
            items_mask,
            split_key,
        )

        # Remove possibly empty items (a dimension may be split to 0).
        items_mask &= ~items_spaces.is_empty()

        return items_spaces, items_mask

    def _split_along_axis(
        self, axis: str, items_spaces: Space, items_mask: Array, key: PRNGKey
    ) -> Tuple[Space, Array]:
        """Given an axis, sample a space to split proportionally to its length on that axis.
        Then, either split the space in two different spaces or split it into a several
        identical sub-spaces.
        """
        current_items = item_from_space(items_spaces)
        item_length = getattr(current_items, f"{axis}_len")
        item_key, split_key, mode_key = jax.random.split(key, 3)
        item_id = jax.random.choice(
            item_key,
            jnp.arange(0, len(items_mask)),
            (),
            p=jnp.where(items_mask, item_length, 0),
        )
        item_space = tree_slice(items_spaces, item_id)
        item_axis_1 = item_space.get_axis_value(axis, 1)
        item_axis_2 = item_space.get_axis_value(axis, 2)
        axis_len = item_axis_2 - item_axis_1

        items_spaces, items_mask = jax.lax.cond(
            jax.random.uniform(mode_key) < self._prob_split_one_item,
            functools.partial(
                self._split_item_once, item_space, axis, axis_len, item_id
            ),
            functools.partial(
                self._split_item_multiple_times, item_space, axis, axis_len, item_id
            ),
            items_spaces,
            items_mask,
            split_key,
        )
        return items_spaces, items_mask

    def _split_item_once(
        self,
        item_space: Space,
        axis: str,
        axis_len: int,
        item_id: int,
        items_spaces: Space,
        items_mask: Array,
        split_key: PRNGKey,
    ) -> Tuple[Space, Array]:
        """Split an item into two sub-spaces. The split position is sampled uniformly on the
        space axis length with paddings equal to `_split_eps`% on each side of the space.
        """
        axis_min, axis_max = (
            item_space.get_axis_value(axis, 1) + jnp.int32(self._split_eps * axis_len),
            item_space.get_axis_value(axis, 2) - jnp.int32(self._split_eps * axis_len),
        )
        axis_split = jax.random.randint(split_key, (), axis_min, axis_max, jnp.int32)
        free_index = jnp.argmin(items_mask)
        items_spaces = jax.tree_map(
            lambda coord: coord.at[free_index].set(coord[item_id]), items_spaces
        )
        new_axis_2 = items_spaces.get_axis_value(axis, 2).at[item_id].set(axis_split)
        items_spaces.set_axis_value(axis, 2, new_axis_2)
        new_axis_1 = items_spaces.get_axis_value(axis, 1).at[free_index].set(axis_split)
        items_spaces.set_axis_value(axis, 1, new_axis_1)
        items_mask = items_mask.at[free_index].set(True)
        return items_spaces, items_mask

    def _split_item_multiple_times(
        self,
        item_space: Space,
        axis: str,
        axis_len: int,
        item_id: int,
        items_spaces: Space,
        items_mask: Array,
        split_key: PRNGKey,
    ) -> Tuple[Space, Array]:
        """Split an item into several identical sub-spaces. The number of these spaces is
        uniformly sampled between 0 and `_split_num_same_items`.
        """
        initial_item_axis_1 = item_space.get_axis_value(axis, 1)
        num_split = jax.random.randint(split_key, (), 1, self._split_num_same_items + 1)

        # Shortens first item before the others as its mask is already True.
        new_items_axis_2 = (
            items_spaces.get_axis_value(axis, 2)
            .at[item_id]
            .set(initial_item_axis_1 + jnp.int32(axis_len / num_split))
        )
        items_spaces.set_axis_value(axis, 2, new_items_axis_2)

        def body_fn(i: int, carry: Tuple[Space, Array]) -> Tuple[Space, Array]:
            items_spaces, items_mask = carry
            free_index = jnp.argmin(items_mask)
            items_spaces = jax.tree_map(
                lambda coord: coord.at[free_index].set(coord[item_id]),
                items_spaces,
            )
            item_axis_1 = initial_item_axis_1 + jnp.int32(i * axis_len / num_split)
            item_axis_2 = initial_item_axis_1 + jnp.int32(
                (i + 1) * axis_len / num_split
            )
            new_items_axis_1 = (
                items_spaces.get_axis_value(axis, 1).at[free_index].set(item_axis_1)
            )
            items_spaces.set_axis_value(axis, 1, new_items_axis_1)
            new_items_axis_2 = (
                items_spaces.get_axis_value(axis, 2).at[free_index].set(item_axis_2)
            )
            items_spaces.set_axis_value(axis, 2, new_items_axis_2)
            items_mask = items_mask.at[free_index].set(True)
            return items_spaces, items_mask

        # Loop over identical items to create from splitting the original item into several ones
        items_spaces, items_mask = jax.lax.fori_loop(
            1,
            num_split,
            body_fn,
            (items_spaces, items_mask),
        )

        return items_spaces, items_mask
